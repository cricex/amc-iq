<#
.SYNOPSIS
  Provision SCOPED Fabric Data Agents for the AMC IQ specialist team, entirely via REST.
.DESCRIPTION
  The single shared eligibility Data Agent becomes a concurrency bottleneck when
  several live specialists query it at once (they queue on one F64-backed item and time out). This
  creates purpose-scoped Data Agents over the SAME Lakehouse, each selecting only its neighborhood of
  tables with focused AI instructions, so specialist calls fan out across multiple items instead of
  one. Matches the scoped-agent design in services/api/app/agents/registry.py.

  Scopes (data_agent name in registry.py -> item):
    labs_renal          -> <prefix>-labs-renal          (renal/labs + genomics facts)
    trials_criteria     -> <prefix>-trials-criteria     (criteria, amendments, prior therapy)
    workflow_scheduling -> <prefix>-workflow-scheduling (ownership, capacity, scheduling)

  Writes ignored local output agent/provisioning/live/scoped_data_agents.local mapping scope to
  { id, mcp }. The tracked scoped_data_agents.json file is a nonfunctional public template.

  Auth: your `az` login (Fabric token). Requires Contributor+ on the workspace and the backing F64
  capacity Active. Idempotent: get-or-create by displayName, re-select tables, re-publish. The Fabric
  public REST API intermittently returns transient RequestFailed, so every call is retried.
.PARAMETER Only
  Provision just one scope (labs_renal | trials_criteria | workflow_scheduling). Default: all.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$WorkspaceId,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$LakehouseName,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$AgentNamePrefix,
  [ValidateSet("labs_renal","trials_criteria","workflow_scheduling")]
  [string]$Only = ""
)
$ErrorActionPreference = "Stop"

function Fabric-Token { az account get-access-token --scope "https://api.fabric.microsoft.com/.default" --query accessToken -o tsv }
$script:tok = Fabric-Token
$api = "https://api.fabric.microsoft.com/v1/workspaces/$WorkspaceId"

# Every Fabric REST call, with retry on the transient "RequestFailed" the API throws under load.
function Invoke-Fabric {
  param([string]$Method = "Get", [string]$Uri, $Body)
  $json = if ($null -ne $Body) { ($Body | ConvertTo-Json -Depth 8) } else { $null }
  for ($attempt = 1; $attempt -le 6; $attempt++) {
    try {
      $h = @{ Authorization = "Bearer $script:tok"; "Content-Type" = "application/json" }
      if ($json) { return Invoke-RestMethod -Method $Method -Headers $h -Uri $Uri -Body $json }
      else       { return Invoke-RestMethod -Method $Method -Headers $h -Uri $Uri }
    } catch {
      if ($attempt -eq 6) { throw }
      Start-Sleep -Seconds ([Math]::Min(20, 4 * $attempt))
      $script:tok = Fabric-Token   # refresh token in case it lapsed
    }
  }
}

# scope -> selected tables + focused instructions
$Scopes = [ordered]@{
  labs_renal = @{
    agent  = "$AgentNamePrefix-labs-renal"
    tables = @("patient_registry","labs","biomarkers","comorbidities","adverse_events","recist_assessments")
    instr  = "You answer questions about synthetic oncology patients' LABS, RENAL FUNCTION, and MOLECULAR/BIOMARKER status from the Lakehouse. labs (lab_date/lab_type/value; CrCl_CKD-EPI is renal function in mL/min; return the most recent CrCl_CKD-EPI row for the latest value, and the prior one for the trend). biomarkers (patient_id, marker, status; e.g. EGFR exon 20 insertion = Detected). patient_registry (ecog_ps, cancer_type, stage). comorbidities, adverse_events, recist_assessments for context. Patient IDs look like PT-1042. Give precise values with dates. Do not give medical advice."
  }
  trials_criteria = @{
    agent  = "$AgentNamePrefix-trials-criteria"
    tables = @("trials","trial_criteria","amendments","patient_registry","treatment_history","biomarkers","consent")
    instr  = "You answer questions about clinical-trial ELIGIBILITY CRITERIA, AMENDMENTS, and PRIOR THERAPY from the Lakehouse. trials (trial_id, crcl_min, ecog_max, biomarker_required, cancer_type). trial_criteria (criterion_id, kind inclusion/exclusion, category, description, references_entity/param/comparator/value). amendments (which criterion_id an amendment modifies, e.g. prior-platinum exclusion clarified by Amendment 2). treatment_history (prior therapies incl platinum doublets). patient_registry + biomarkers for the patient's facts. Trial IDs look like NCT99004324; patients like PT-1042. Do not give medical advice."
  }
  workflow_scheduling = @{
    agent  = "$AgentNamePrefix-workflow-scheduling"
    tables = @("patient_registry","people","sites","coordinator_workload","scheduling_slots","trial_enrollment")
    instr  = "You answer questions about care-team OWNERSHIP, CAPACITY, and SCHEDULING from the Lakehouse. patient_registry (coordinator_id, treating_oncologist_id = the owners). people/sites (display names for those ids). coordinator_workload keyed by coordinator_id (open_screenings, pending_tasks, capacity_this_week). scheduling_slots (assigned_patient_id, site_id, slot_type Screening/'PI consult', date, time, available). trial_enrollment for screening status. Scheduling is by patient and site, not by trial. Patients look like PT-1042. Do not give medical advice."
  }
}

$lh = (Invoke-Fabric -Uri "$api/lakehouses").value | Where-Object displayName -eq $LakehouseName
if (-not $lh) { throw "Lakehouse '$LakehouseName' not found. Run provision_fabric_data_agent.ps1 first." }
$lhId = $lh.id
Write-Host "Lakehouse $LakehouseName ($lhId)" -ForegroundColor Green

function Provision-Scoped($scopeKey, $def) {
  $agentName = $def.agent
  Write-Host "`n== $scopeKey -> $agentName ==" -ForegroundColor Cyan

  $da = (Invoke-Fabric -Uri "$api/items").value | Where-Object { $_.type -eq "DataAgent" -and $_.displayName -eq $agentName }
  if (-not $da) {
    $da = Invoke-Fabric -Method Post -Uri "$api/items" -Body @{ displayName = $agentName; type = "DataAgent"; description = "AMC IQ scoped Fabric Data Agent ($scopeKey)." }
  }
  $daId = $da.id
  $b = "$api/dataAgents/$daId"
  Write-Host "   item $daId" -ForegroundColor Green

  $existing = (Invoke-Fabric -Uri "$b/staging/datasources").value
  if (-not ($existing | Where-Object { $_.id -eq $lhId })) {
    Invoke-Fabric -Method Post -Uri "$b/staging/datasources" -Body @{ type = "LakehouseTables"; lakehouseReference = @{ referenceType = "ById"; itemId = $lhId; workspaceId = $WorkspaceId } } | Out-Null
    for ($i=0; $i -lt 12; $i++) { Start-Sleep 5; if ((Invoke-Fabric -Uri "$b/staging/datasources").value.Count -gt 0) { break } }
  }

  $el = "$b/staging/datasources/$lhId/elements"
  $dbo = (Invoke-Fabric -Uri "${el}?rootId=U2NoZW1hcw==").value                 # 'Schemas'
  $tablesC = (Invoke-Fabric -Uri "${el}?rootId=$([uri]::EscapeDataString($dbo[0].id))").value | Where-Object displayName -eq 'Tables'
  $tables = (Invoke-Fabric -Uri "${el}?rootId=$([uri]::EscapeDataString($tablesC.id))").value
  $want = $def.tables
  foreach ($t in $tables) {
    $sel = if ($want -contains $t.displayName) { $true } else { $false }
    Invoke-Fabric -Method Patch -Uri "${el}?id=$([uri]::EscapeDataString($t.id))" -Body @{ isSelected = $sel } | Out-Null
  }
  Write-Host "   selected: $(($tables | Where-Object { $want -contains $_.displayName }).displayName -join ', ')" -ForegroundColor Green

  Invoke-Fabric -Method Patch -Uri "$b/staging/settings" -Body @{ aiInstructions = $def.instr } | Out-Null
  Invoke-Fabric -Method Post -Uri "$b/staging/publish" -Body @{ publishedDescription = "AMC IQ scoped Data Agent ($scopeKey) v1" } | Out-Null
  Write-Host "   PUBLISHED" -ForegroundColor Green

  return @{ id = $daId; mcp = "https://api.fabric.microsoft.com/v1/mcp/workspaces/$WorkspaceId/dataagents/$daId/agent" }
}

$out = [ordered]@{}
foreach ($k in $Scopes.Keys) {
  if ($Only -and $k -ne $Only) { continue }
  $out[$k] = Provision-Scoped $k $Scopes[$k]
}

$jsonPath = Join-Path $PSScriptRoot "scoped_data_agents.local"
if (Test-Path $jsonPath) {
  $prev = Get-Content $jsonPath -Raw | ConvertFrom-Json
  foreach ($p in $prev.PSObject.Properties) { if (-not $out.Contains($p.Name)) { $out[$p.Name] = @{ id = $p.Value.id; mcp = $p.Value.mcp } } }
}
$out | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 $jsonPath
Write-Host "`nWrote $jsonPath" -ForegroundColor Yellow
$out.GetEnumerator() | ForEach-Object { Write-Host ("  {0} -> {1}" -f $_.Key, $_.Value.id) -ForegroundColor Green }
