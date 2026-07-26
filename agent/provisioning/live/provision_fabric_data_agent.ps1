<#
.SYNOPSIS
  Provision the LIVE Fabric IQ layer for the AMC IQ proof-of-concept, VERIFIED working 2026-07-06.
.DESCRIPTION
  Creates, configures, and PUBLISHES a Fabric Data Agent entirely via REST (no GUI):
    1. Lakehouse `amciq_fabric_oncology_clinical` in the AMC IQ workspace
    2. Load data/fabric/*.csv as Delta tables  (see load_fabric_lakehouse.py)
    3. Operator-named Data Agent item
    4. Reconcile the agent to the Lakehouse as its only staging datasource + select all tables
    5. Set AI instructions + publish

  Auth: uses your `az` login (Fabric token). Requires Contributor+ on the Fabric workspace.
  The Fabric public REST API (api.fabric.microsoft.com/v1/workspaces/{ws}/dataAgents/{da}) is used
  directly, the fabric-data-agent-sdk requires a notebook runtime and is NOT needed here.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$WorkspaceId,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$LakehouseName,
  [string]$LakehouseId = "",
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$DataAgentName,
  [string]$DataAgentId = "",
  [string]$CsvDir = "",   # defaults to <repo>/data/fabric
  [switch]$PrepareOnly
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
if (-not $CsvDir) { $CsvDir = Join-Path $repo "data\fabric" }
function Fabric-Token { az account get-access-token --scope "https://api.fabric.microsoft.com/.default" --query accessToken -o tsv }
function H { @{ Authorization = "Bearer $(Fabric-Token)"; "Content-Type" = "application/json" } }
$api = "https://api.fabric.microsoft.com/v1/workspaces/$WorkspaceId"

Write-Host "1) Lakehouse" -ForegroundColor Cyan
if ($LakehouseId) {
  $lhId = $LakehouseId
} else {
  $lh = (Invoke-RestMethod -Headers (H) -Uri "$api/lakehouses").value | Where-Object displayName -eq $LakehouseName
  if (-not $lh) {
    $body = @{ displayName = $LakehouseName; description = "AMC IQ synthetic clinical/operational oncology data (Fabric IQ)." } | ConvertTo-Json
    $lh = (Invoke-WebRequest -Method Post -Headers (H) -Uri "$api/lakehouses" -Body $body).Content | ConvertFrom-Json
  }
  $lhId = $lh.id
}
Write-Host "   lakehouse=$LakehouseName ($lhId)" -ForegroundColor Green
Write-Host "   -> load CSVs as Delta tables with: python agent/provisioning/live/load_fabric_lakehouse.py" -ForegroundColor Yellow

Write-Host "2) Data Agent item" -ForegroundColor Cyan
if ($DataAgentId) {
  $daId = $DataAgentId
} else {
  $da = (Invoke-RestMethod -Headers (H) -Uri "$api/items").value | Where-Object { $_.type -eq "DataAgent" -and $_.displayName -eq $DataAgentName }
  if (-not $da) {
    $body = @{ displayName = $DataAgentName; type = "DataAgent"; description = "AMC IQ Fabric Data Agent over synthetic clinical data." } | ConvertTo-Json
    $da = (Invoke-WebRequest -Method Post -Headers (H) -Uri "$api/items" -Body $body).Content | ConvertFrom-Json
  }
  $daId = $da.id
}
$b = "$api/dataAgents/$daId"
Write-Host "   data agent=$DataAgentName ($daId)" -ForegroundColor Green

Write-Host "3) Add Lakehouse datasource" -ForegroundColor Cyan
$existing = (Invoke-RestMethod -Headers (H) -Uri "$b/staging/datasources").value
$lakehouseSource = $existing | Where-Object {
  $_.id -eq $lhId -or
  $_.itemReference.itemId -eq $lhId -or
  $_.lakehouseReference.itemId -eq $lhId
} | Select-Object -First 1
if (-not $lakehouseSource) {
  $body = @{ type = "LakehouseTables"; lakehouseReference = @{ referenceType = "ById"; itemId = $lhId; workspaceId = $WorkspaceId } } | ConvertTo-Json -Depth 6
  Invoke-WebRequest -Method Post -Headers (H) -Uri "$b/staging/datasources" -Body $body | Out-Null
  for ($i=0; $i -lt 12; $i++) {
    Start-Sleep 5
    $existing = (Invoke-RestMethod -Headers (H) -Uri "$b/staging/datasources").value
    $lakehouseSource = $existing | Where-Object {
      $_.id -eq $lhId -or
      $_.itemReference.itemId -eq $lhId -or
      $_.lakehouseReference.itemId -eq $lhId
    } | Select-Object -First 1
    if ($lakehouseSource) { break }
  }
  if (-not $lakehouseSource) { throw "Lakehouse datasource did not appear on $DataAgentName." }
}
Write-Host "   datasource added" -ForegroundColor Green

Write-Host "4) Remove non-Lakehouse datasources" -ForegroundColor Cyan
$existing = (Invoke-RestMethod -Headers (H) -Uri "$b/staging/datasources").value
$unexpected = @($existing | Where-Object { $_.id -ne $lakehouseSource.id })
foreach ($source in $unexpected) {
  Invoke-WebRequest -Method Delete -Headers (H) -Uri "$b/staging/datasources/$($source.id)" | Out-Null
  Write-Host "   removed datasource $($source.id)" -ForegroundColor Yellow
}
if ($unexpected) {
  for ($i=0; $i -lt 12; $i++) {
    Start-Sleep 5
    $remaining = @(
      (Invoke-RestMethod -Headers (H) -Uri "$b/staging/datasources").value |
        Where-Object { $_.id -ne $lakehouseSource.id }
    )
    if (-not $remaining) { break }
  }
  if ($remaining) { throw "Extra datasources remain on $DataAgentName after reconciliation." }
}
$finalSources = @((Invoke-RestMethod -Headers (H) -Uri "$b/staging/datasources").value)
if ($finalSources.Count -ne 1 -or $finalSources[0].id -ne $lakehouseSource.id) {
  throw "Expected exactly one Lakehouse datasource on $DataAgentName after reconciliation."
}
Write-Host "   Lakehouse-only datasource contract enforced" -ForegroundColor Green

if ($PrepareOnly) {
  Write-Host "   preparation complete; load Lakehouse tables before publishing" -ForegroundColor Yellow
  return
}

Write-Host "5) Select all tables" -ForegroundColor Cyan
$el = "$b/staging/datasources/$($lakehouseSource.id)/elements"
$requiredTables = @(
  "adverse_events",
  "amendments",
  "biomarkers",
  "comorbidities",
  "consent",
  "coordinator_workload",
  "labs",
  "patient_registry",
  "people",
  "recist_assessments",
  "scheduling_slots",
  "sites",
  "treatment_history",
  "trial_criteria",
  "trial_enrollment",
  "trials"
)
$tables = @()
$missingTables = $requiredTables
for ($i=0; $i -lt 12; $i++) {
  try {
    $dbo = (Invoke-RestMethod -Headers (H) -Uri "${el}?rootId=U2NoZW1hcw==").value
    $tablesContainer = (Invoke-RestMethod -Headers (H) -Uri "${el}?rootId=$([uri]::EscapeDataString($dbo[0].id))").value |
      Where-Object displayName -eq "Tables" |
      Select-Object -First 1
    $tables = @(
      (Invoke-RestMethod -Headers (H) -Uri "${el}?rootId=$([uri]::EscapeDataString($tablesContainer.id))").value |
        Where-Object { -not $_.state -or $_.state -eq "Available" }
    )
    $availableNames = @($tables | ForEach-Object displayName)
    $missingTables = @($requiredTables | Where-Object { $_ -notin $availableNames })
  } catch {
    $tables = @()
    $missingTables = $requiredTables
  }
  if (-not $missingTables) { break }
  if ($i -lt 11) { Start-Sleep 5 }
}
if ($missingTables) {
  throw "Lakehouse datasource is missing required eligibility tables: $($missingTables -join ', ')."
}
foreach ($t in $tables) {
  if ($t.isSelected -ne $true) {
    Invoke-RestMethod -Method Patch -Headers (H) -Uri "${el}?id=$([uri]::EscapeDataString($t.id))" -Body (@{ isSelected = $true } | ConvertTo-Json) | Out-Null
  }
}
$missingSelections = $requiredTables
for ($i=0; $i -lt 12; $i++) {
  $selected = @(
    (Invoke-RestMethod -Headers (H) -Uri "${el}?rootId=$([uri]::EscapeDataString($tablesContainer.id))").value |
      Where-Object { $_.isSelected -eq $true } |
      ForEach-Object displayName
  )
  $missingSelections = @($requiredTables | Where-Object { $_ -notin $selected })
  if (-not $missingSelections) { break }
  if ($i -lt 11) { Start-Sleep 5 }
}
if ($missingSelections) {
  throw "Required eligibility table selections did not propagate: $($missingSelections -join ', ')."
}
Write-Host "   selected: $($tables.displayName -join ', ')" -ForegroundColor Green

Write-Host "6) Instructions + publish" -ForegroundColor Cyan
$instr = "You answer questions about synthetic oncology patients and clinical trial operations using the Lakehouse tables. patient_registry (demographics, ECOG, diagnosis, stage), labs (lab_date/lab_type/value; CrCl_CKD-EPI is renal function in mL/min), treatment_history (prior therapies incl platinum doublets), trials (trial_id, crcl_min, ecog_max, biomarker_required), trial_enrollment, coordinator_workload, scheduling_slots. Patient IDs look like PT-1042; trial IDs like NCT99004324. For a patient's latest CrCl, return the most recent labs row where lab_type='CrCl_CKD-EPI'. Give precise values with dates. Do not give medical advice."
Invoke-RestMethod -Method Patch -Headers (H) -Uri "$b/staging/settings" -Body (@{ aiInstructions = $instr } | ConvertTo-Json) | Out-Null
Invoke-WebRequest -Method Post -Headers (H) -Uri "$b/staging/publish" -Body (@{ publishedDescription = "AMC IQ Fabric Data Agent v1" } | ConvertTo-Json) | Out-Null
Write-Host "   PUBLISHED." -ForegroundColor Green
Write-Host "   workspaceId=$WorkspaceId  dataAgentId=$daId" -ForegroundColor Yellow
