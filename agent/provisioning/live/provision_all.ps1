<#
.SYNOPSIS
  Provision the AMC IQ live IQ service layer from zero, idempotently.
.DESCRIPTION
  Safe to re-run: every step checks-then-creates. Covers:
    1.  Operator-selected Foundry account + model deployments
    2.  Storage account + container + upload data/foundry_docs
    3.  Foundry IQ: operator-named azureBlob knowledge source + knowledge base
    4.  Web IQ: operator-named web knowledge source + knowledge base
    5.  Operator-selected Foundry project (+ managed identity)
    6.  Role assignments: project MI -> Search Index Data Reader/Contributor on the Search service
    7.  Operator-named RemoteTool MCP connections
    8.  Fabric IQ: Lakehouse + Delta tables + published Data Agent (delegates to the Fabric scripts)
    8b. Fabric IQ Ontology: real Ontology item (entity + relationship types) bound to the Lakehouse,
        with Data Agent attachment gated until the child GraphModel has valid content
    9.  Fabric IQ wiring: add project MI to the Fabric workspace + operator-named MCP connection
    10. Hosted agent amciq-agent-eligibility (Foundry IQ + Web IQ; Fabric MCP tool via -IncludeFabric)

  Auth: your `az` login (device login supported: `az login --use-device-code`). Requires Owner (or
  Contributor + User Access Administrator) on the subscription and Search Service/Index Contributor on
  the Search service, plus Contributor on the Fabric workspace.

  Prereqs: az CLI, Python venv at repo .venv with services/api/requirements.txt installed
  (azure-ai-projects, azure-identity), and the Fabric loader deps (requests, azure-storage-file-datalake).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Subscription,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceGroup,
  [string]$Location       = "eastus2",
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryAccount,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Project,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$SearchService,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$SearchRg,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Container,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ChatDeployment,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ChatModel,
  [string]$ChatVersion    = "2026-03-05",
  [int]$ChatCapacity      = 500,
  [string]$EmbedDeployment = "text-embedding-3-large",
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$KbName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$KsName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$WebKbName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$WebKsName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricWorkspaceId,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricLakehouseName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricDataAgentName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$OntologyName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$GraphModelName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$GraphDataAgentName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$HostedAgentName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$EligibilityEvaluatorAgentName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$SpecialistAgentPrefix,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryConnectionName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$WebConnectionName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricConnectionName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricMcpToolName,
  [string]$ExistingFabricDataAgentId = "",
  [string]$WorkIqSecurityGroupObjectId = "",
  [string]$WorkIqSeederAppName = "",
  [string]$ApiVersion     = "2026-05-01-preview",
  [switch]$IncludeFabric,         # also wire the Fabric Data Agent MCP tool onto the hosted agent
  [switch]$SkipFabricAssets,       # skip Lakehouse/Data Agent creation
  [switch]$IncludeSpecialists,     # also provision the 6 real Foundry specialist agents
  [switch]$IncludeWorkIQSeed       # also provision the M365 seeder app and seed Work IQ proof-of-concept content
)
$ErrorActionPreference = "Stop"
$repo   = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$py     = Join-Path $repo ".venv\Scripts\python.exe"
$tags   = @("project=amc-iq","scenario=foundry-iq","env=poc")
$searchEndpoint = "https://$SearchService.search.windows.net"
az account set --subscription $Subscription | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to select Azure subscription $Subscription."
}

function SearchHeaders { @{ Authorization = "Bearer $(az account get-access-token --scope 'https://search.azure.com/.default' --query accessToken -o tsv)"; "Content-Type"="application/json" } }
function ArmHeaders    { @{ Authorization = "Bearer $(az account get-access-token --scope 'https://management.azure.com/.default' --query accessToken -o tsv)"; "Content-Type"="application/json" } }
function Exists($uri, $hdr) { try { Invoke-RestMethod -Headers $hdr -Uri $uri | Out-Null; return $true } catch { return $false } }

Write-Host "[1] Foundry account + model deployments" -ForegroundColor Cyan
if (-not (az cognitiveservices account show -n $FoundryAccount -g $ResourceGroup 2>$null)) {
  az cognitiveservices account create -n $FoundryAccount -g $ResourceGroup -l $Location --kind AIServices --sku S0 `
     --custom-domain $FoundryAccount --assign-identity --yes --tags $tags | Out-Null
}
$fUri = "https://$FoundryAccount.openai.azure.com"
$fKey = az cognitiveservices account keys list -n $FoundryAccount -g $ResourceGroup --query key1 -o tsv
foreach ($d in @(@{n=$EmbedDeployment;m="text-embedding-3-large";v="1";sku="Standard";cap=120},
                 @{n=$ChatDeployment;m=$ChatModel;v=$ChatVersion;sku="GlobalStandard";cap=$ChatCapacity})) {
  $existingDeployment = az cognitiveservices account deployment show -n $FoundryAccount -g $ResourceGroup `
    --deployment-name $d.n -o json 2>$null
  if (-not $existingDeployment) {
    az cognitiveservices account deployment create -n $FoundryAccount -g $ResourceGroup --deployment-name $d.n `
       --model-name $d.m --model-version $d.v --model-format OpenAI --sku-name $d.sku --sku-capacity $d.cap | Out-Null
  } else {
    $existingDeployment = $existingDeployment | ConvertFrom-Json
    if ([int]$existingDeployment.sku.capacity -ne [int]$d.cap) {
      az resource update --ids $existingDeployment.id --set "sku.capacity=$($d.cap)" | Out-Null
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to update model deployment $($d.n) capacity to $($d.cap)."
      }
    }
  }
}
Write-Host "    ok" -ForegroundColor Green

Write-Host "[2] Storage + upload docs" -ForegroundColor Cyan
$stg = (az storage account list -g $ResourceGroup --query "[?starts_with(name,'amciqdocs')].name | [0]" -o tsv)
if (-not $stg) {
  $stg = "amciqdocs" + (-join ((48..57)+(97..102) | Get-Random -Count 6 | ForEach-Object {[char]$_}))
  az storage account create -n $stg -g $ResourceGroup -l $Location --sku Standard_LRS --kind StorageV2 `
     --allow-blob-public-access false --min-tls-version TLS1_2 --tags $tags | Out-Null
}
$key  = az storage account keys list -n $stg -g $ResourceGroup --query "[0].value" -o tsv
$conn = az storage account show-connection-string -n $stg -g $ResourceGroup --query connectionString -o tsv
az storage container create -n $Container --account-name $stg --account-key $key --only-show-errors | Out-Null
Get-ChildItem (Join-Path $repo "data\foundry_docs") -Filter *.md | ForEach-Object {
  az storage blob upload --account-name $stg --account-key $key -c $Container -f $_.FullName -n $_.Name --overwrite --only-show-errors | Out-Null
}
Write-Host "    storage=$stg" -ForegroundColor Green

Write-Host "[3] Foundry IQ knowledge source + knowledge base" -ForegroundColor Cyan
$hdr = SearchHeaders
$ks = @{ name=$KsName; kind="azureBlob"
  description="AMC IQ synthetic oncology institutional knowledge."
  azureBlobParameters=@{ connectionString=$conn; containerName=$Container; ingestionParameters=@{
    contentExtractionMode="minimal"
    embeddingModel=@{ kind="azureOpenAI"; azureOpenAIParameters=@{ resourceUri=$fUri; deploymentId=$EmbedDeployment; apiKey=$fKey; modelName="text-embedding-3-large" } }
    chatCompletionModel=@{ kind="azureOpenAI"; azureOpenAIParameters=@{ resourceUri=$fUri; deploymentId=$ChatDeployment; apiKey=$fKey; modelName=$ChatModel } } } } } | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Put -Headers $hdr -Uri "$searchEndpoint/knowledgesources/$KsName`?api-version=$ApiVersion" -Body $ks | Out-Null
$kb = @{ name=$KbName; description="AMC IQ precision-oncology knowledge base."
  retrievalInstructions="Institutional clinical-trial knowledge: eligibility criteria (renal thresholds, prior-therapy exclusions), amendments, IRB/consent, SOPs, patient notes/pathology/genomics. Preserve IDs like NCT99004324 and PT-1042."
  answerInstructions="Answer concisely, grounded only in retrieved content, with citations. Flag borderline renal values and PI-confirmation needs. Never give a definitive medical decision; recommend human review."
  outputMode="answerSynthesis"; knowledgeSources=@(@{ name=$KsName })
  models=@(@{ kind="azureOpenAI"; azureOpenAIParameters=@{ resourceUri=$fUri; deploymentId=$ChatDeployment; apiKey=$fKey; modelName=$ChatModel } })
  retrievalReasoningEffort=@{ kind="medium" } } | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Put -Headers $hdr -Uri "$searchEndpoint/knowledgebases/$KbName`?api-version=$ApiVersion" -Body $kb | Out-Null
Write-Host "    kb=$KbName" -ForegroundColor Green

Write-Host "[4] Web IQ knowledge source + knowledge base" -ForegroundColor Cyan
$webKs = @{ name=$WebKsName; kind="web"; description="Live external web grounding (Web IQ)." } | ConvertTo-Json
Invoke-RestMethod -Method Put -Headers (SearchHeaders) -Uri "$searchEndpoint/knowledgesources/$WebKsName`?api-version=$ApiVersion" -Body $webKs | Out-Null
$webKb = @{ name=$WebKbName; description="Web IQ: fresh external web grounding."
  retrievalInstructions="Public web for external oncology context: treatment landscape and standard of care."
  answerInstructions="Summarize external web context with source URLs. Frame as external/public context. No definitive medical decisions."
  outputMode="answerSynthesis"; knowledgeSources=@(@{ name=$WebKsName })
  models=@(@{ kind="azureOpenAI"; azureOpenAIParameters=@{ resourceUri=$fUri; deploymentId=$ChatDeployment; apiKey=$fKey; modelName=$ChatModel } })
  retrievalReasoningEffort=@{ kind="low" } } | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Put -Headers (SearchHeaders) -Uri "$searchEndpoint/knowledgebases/$WebKbName`?api-version=$ApiVersion" -Body $webKb | Out-Null
Write-Host "    webkb=$WebKbName" -ForegroundColor Green

Write-Host "[5] Foundry project" -ForegroundColor Cyan
$projMi = az cognitiveservices account project show -n $FoundryAccount -g $ResourceGroup --project-name $Project --query identity.principalId -o tsv 2>$null
if (-not $projMi) {
  az cognitiveservices account project create -n $FoundryAccount -g $ResourceGroup --project-name $Project -l $Location --assign-identity | Out-Null
  $projMi = az cognitiveservices account project show -n $FoundryAccount -g $ResourceGroup --project-name $Project --query identity.principalId -o tsv
}
Write-Host "    project MI=$projMi" -ForegroundColor Green

Write-Host "[6] Role assignments (project MI -> Search)" -ForegroundColor Cyan
$searchId = az search service show -n $SearchService -g $SearchRg --query id -o tsv
foreach ($role in @("Search Index Data Reader","Search Service Contributor")) {
  az role assignment create --assignee-object-id $projMi --assignee-principal-type ServicePrincipal --role $role --scope $searchId 2>$null | Out-Null
}
Write-Host "    ok" -ForegroundColor Green

Write-Host "[7] MCP RemoteTool connections" -ForegroundColor Cyan
$projArm = "/subscriptions/$Subscription/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccount/projects/$Project"
$projectEndpoint = "https://$FoundryAccount.services.ai.azure.com/api/projects/$Project"
$env:PROJECT_ENDPOINT = $projectEndpoint
$env:KB_MCP_ENDPOINT = "$searchEndpoint/knowledgebases/$KbName/mcp?api-version=$ApiVersion"
$env:WEB_MCP_ENDPOINT = "$searchEndpoint/knowledgebases/$WebKbName/mcp?api-version=$ApiVersion"
$env:AGENT_MODEL = $ChatDeployment
$env:FABRIC_WORKSPACE_ID = $FabricWorkspaceId
$env:KB_CONNECTION = $FoundryConnectionName
$env:WEB_CONNECTION = $WebConnectionName
$env:FABRIC_CONNECTION = $FabricConnectionName
$env:FABRIC_MCP_TOOL = $FabricMcpToolName
$env:AGENT_NAME = $HostedAgentName
$env:EVALUATOR_AGENT_NAME = $EligibilityEvaluatorAgentName
$env:ELIGIBILITY_EVALUATOR_AGENT = $EligibilityEvaluatorAgentName
$env:SPECIALIST_AGENT_PREFIX = $SpecialistAgentPrefix
$env:SPECIALIST_ELIGIBILITY_AGENT = "$SpecialistAgentPrefix-eligibility"
$env:SPECIALIST_RENAL_AGENT = "$SpecialistAgentPrefix-renal-labs"
$env:SPECIALIST_GENOMICS_AGENT = "$SpecialistAgentPrefix-genomics"
$env:SPECIALIST_PROTOCOL_AGENT = "$SpecialistAgentPrefix-protocol"
$env:SPECIALIST_WORKFLOW_AGENT = "$SpecialistAgentPrefix-workflow"
$env:SPECIALIST_EVIDENCE_AGENT = "$SpecialistAgentPrefix-evidence"
$env:ONTOLOGY_NAME = $OntologyName
$env:DATA_AGENT_NAME = $FabricDataAgentName
$env:GRAPH_MODEL_NAME = $GraphModelName
$env:GRAPH_DATA_AGENT_NAME = $GraphDataAgentName
$env:ELIGIBILITY_DATA_AGENT_NAME = $FabricDataAgentName
function PutConnection($name, $mcpUrl) {
  $body = @{ properties=@{ authType="ProjectManagedIdentity"; category="RemoteTool"; target=$mcpUrl; isSharedToAll=$true; audience="https://search.azure.com/"; metadata=@{ ApiType="Azure" } } } | ConvertTo-Json -Depth 6
  Invoke-RestMethod -Method Put -Headers (ArmHeaders) -Uri "https://management.azure.com$projArm/connections/$name`?api-version=2025-10-01-preview" -Body $body | Out-Null
}
PutConnection $FoundryConnectionName "$searchEndpoint/knowledgebases/$KbName/mcp?api-version=$ApiVersion"
PutConnection $WebConnectionName     "$searchEndpoint/knowledgebases/$WebKbName/mcp?api-version=$ApiVersion"
Write-Host "    connections created" -ForegroundColor Green

if (-not $SkipFabricAssets) {
  Write-Host "[8] Fabric IQ assets (Lakehouse + tables + Data Agent)" -ForegroundColor Cyan
  & "$PSScriptRoot\provision_fabric_data_agent.ps1" `
    -WorkspaceId $FabricWorkspaceId `
    -LakehouseName $FabricLakehouseName `
    -DataAgentName $FabricDataAgentName `
    -PrepareOnly
  if ($LASTEXITCODE -ne 0) {
    throw "Fabric Data Agent preparation failed with exit code $LASTEXITCODE."
  }
  $lhId = (Invoke-RestMethod -Headers @{Authorization="Bearer $(az account get-access-token --scope 'https://api.fabric.microsoft.com/.default' --query accessToken -o tsv)"} -Uri "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/lakehouses").value `
         | Where-Object displayName -eq $FabricLakehouseName | Select-Object -First 1
  $env:FABRIC_WS=$FabricWorkspaceId; $env:FABRIC_LH=$lhId.id
  $env:ONELAKE_TOKEN=az account get-access-token --scope "https://storage.azure.com/.default" --query accessToken -o tsv
  $env:FABRIC_TOKEN=az account get-access-token --scope "https://api.fabric.microsoft.com/.default" --query accessToken -o tsv
  & $py "$PSScriptRoot\load_fabric_lakehouse.py"
  if ($LASTEXITCODE -ne 0) {
    throw "Fabric Lakehouse loading failed with exit code $LASTEXITCODE."
  }
  & "$PSScriptRoot\provision_fabric_data_agent.ps1" `
    -WorkspaceId $FabricWorkspaceId `
    -LakehouseName $FabricLakehouseName `
    -LakehouseId $lhId.id `
    -DataAgentName $FabricDataAgentName
  if ($LASTEXITCODE -ne 0) {
    throw "Fabric Data Agent publishing failed with exit code $LASTEXITCODE."
  }
  $daId = (Invoke-RestMethod -Headers @{Authorization="Bearer $env:FABRIC_TOKEN"} -Uri "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/items").value `
          | Where-Object { $_.type -eq "DataAgent" -and $_.displayName -eq $FabricDataAgentName } | Select-Object -First 1
  if (-not $daId -or -not $daId.id) {
    throw "Published Fabric eligibility Data Agent could not be resolved."
  }
  $env:FABRIC_DATA_AGENT_ID = $daId.id
  $env:ELIGIBILITY_DATA_AGENT_ID = $daId.id

  if ($env:ATTACH_ONTOLOGY_TO_DA) {
    Write-Host "[8b] Fabric IQ Ontology (item + bindings + Data Agent attachment)" -ForegroundColor Cyan
  } else {
    Write-Host "[8b] Fabric IQ Ontology (item + bindings; Data Agent attachment gated)" -ForegroundColor Cyan
  }
  $env:STORAGE_TOKEN=$env:ONELAKE_TOKEN
  Remove-Item Env:\ONTOLOGY_ID -ErrorAction SilentlyContinue
  & $py "$PSScriptRoot\provision_fabric_ontology.py"
  if ($LASTEXITCODE -ne 0) {
    throw "Fabric Ontology provisioning failed with exit code $LASTEXITCODE."
  }
  if ($env:ATTACH_ONTOLOGY_TO_DA) {
    Write-Host "    ontology provisioned and attached" -ForegroundColor Green
  } else {
    Write-Host "    ontology provisioned; Data Agent attachment skipped" -ForegroundColor Green
  }

  if ($env:ATTACH_GRAPH_TO_DA -eq "1") {
    Write-Host "[8c] Fabric GraphModel (direct GQL + dedicated graph-only Data Agent)" -ForegroundColor Cyan
  } else {
    Write-Host "[8c] Fabric GraphModel (direct GQL; graph Data Agent attachment gated)" -ForegroundColor Cyan
  }
  & $py "$PSScriptRoot\provision_fabric_graph.py"
  if ($LASTEXITCODE -ne 0) {
    throw "Fabric GraphModel provisioning failed with exit code $LASTEXITCODE."
  }
  if ($env:ATTACH_GRAPH_TO_DA -eq "1") {
    Write-Host "    GraphModel and dedicated graph Data Agent verified" -ForegroundColor Green
  } else {
    Write-Host "    GraphModel direct GQL verified; graph Data Agent skipped" -ForegroundColor Green
  }

  Write-Host "[9] Fabric IQ wiring (workspace membership + MCP connection)" -ForegroundColor Cyan
  $daId = (Invoke-RestMethod -Headers @{Authorization="Bearer $($env:FABRIC_TOKEN)"} -Uri "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/items").value `
          | Where-Object { $_.type -eq "DataAgent" -and $_.displayName -eq $FabricDataAgentName } | Select-Object -First 1
  # a) Grant the Foundry project managed identity (an SPN) access to the Fabric workspace so its
  #    token can call the Data Agent MCP endpoint. Idempotent: throws if the assignment already exists.
  $fabTok = az account get-access-token --scope "https://api.fabric.microsoft.com/.default" --query accessToken -o tsv
  $raBody = @{ principal=@{ id=$projMi; type="ServicePrincipal" }; role="Member" } | ConvertTo-Json
  try { Invoke-RestMethod -Method Post -Headers @{Authorization="Bearer $fabTok";"Content-Type"="application/json"} -Uri "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/roleAssignments" -Body $raBody | Out-Null }
  catch { Write-Host "    (project MI workspace membership already present)" -ForegroundColor DarkGray }
  # b) RemoteTool MCP connection to the published Data Agent (ProjectManagedIdentity, Fabric audience).
  #    This replaces the older AzureFabric/CustomKeys preview-tool path (OBO runtime boundary).
  $fabMcpUrl = "https://api.fabric.microsoft.com/v1/mcp/workspaces/$FabricWorkspaceId/dataagents/$($daId.id)/agent"
  $fbody = @{ properties=@{ authType="ProjectManagedIdentity"; category="RemoteTool"; target=$fabMcpUrl; isSharedToAll=$true; audience="https://api.fabric.microsoft.com"; metadata=@{ ApiType="Azure"; workspaceId=$FabricWorkspaceId; artifactId=$daId.id } } } | ConvertTo-Json -Depth 6
  Invoke-RestMethod -Method Put -Headers (ArmHeaders) -Uri "https://management.azure.com$projArm/connections/$FabricConnectionName`?api-version=2025-10-01-preview" -Body $fbody | Out-Null
  Write-Host "    fabric assets + MCP connection ok (dataAgent=$($daId.id))" -ForegroundColor Green
}

Write-Host "[10] Hosted agent" -ForegroundColor Cyan
if ($IncludeFabric) { $env:INCLUDE_FABRIC="true" } else { $env:INCLUDE_FABRIC="false" }
if ($IncludeFabric -and $SkipFabricAssets) {
  if (-not $ExistingFabricDataAgentId) {
    throw "-ExistingFabricDataAgentId is required when -IncludeFabric and -SkipFabricAssets are used together."
  }
  $env:FABRIC_DATA_AGENT_ID = $ExistingFabricDataAgentId
} elseif ($IncludeFabric -and -not $env:FABRIC_DATA_AGENT_ID) {
  throw "Fabric assets were provisioned but the eligibility Data Agent id was not resolved."
}
& $py "$PSScriptRoot\create_agent.py"
if ($LASTEXITCODE -ne 0) {
  throw "Hosted agent provisioning failed with exit code $LASTEXITCODE."
}

if ($IncludeSpecialists) {
  Write-Host "[11] Real Foundry specialist agents (eligibility, renal-labs, genomics, protocol, workflow, evidence)" -ForegroundColor Cyan
  if ($IncludeFabric) { $env:INCLUDE_FABRIC="true" } else { $env:INCLUDE_FABRIC="false" }
  if (-not $IncludeFabric) {
    Write-Host "    NOTE: the workflow specialist needs the Fabric tool; pass -IncludeFabric or provisioning will stop on it." -ForegroundColor Yellow
  }
  & $py "$PSScriptRoot\create_specialists.py"
  if ($LASTEXITCODE -ne 0) {
    throw "Specialist agent provisioning failed with exit code $LASTEXITCODE."
  }
  Write-Host "    specialists provisioned" -ForegroundColor Green
}

Write-Host "[12] Foundry eligibility-evaluator agent (Fabric-native eligibility)" -ForegroundColor Cyan
& $py "$PSScriptRoot\create_eligibility_evaluator.py"
if ($LASTEXITCODE -ne 0) {
  throw "Eligibility evaluator provisioning failed with exit code $LASTEXITCODE."
}
Write-Host "    evaluator provisioned" -ForegroundColor Green

if ($IncludeWorkIQSeed) {
  Write-Host "[13] Work IQ proof-of-concept content seed (dedicated M365 device-code seeder)" -ForegroundColor Cyan
  Write-Host "    provisions the operator-selected least-privilege delegated Graph app and seeds synthetic mail/calendar/To Do" -ForegroundColor DarkGray
  if (-not $WorkIqSecurityGroupObjectId) {
    throw "-WorkIqSecurityGroupObjectId is required with -IncludeWorkIQSeed."
  }
  if (-not $WorkIqSeederAppName) {
    throw "-WorkIqSeederAppName is required with -IncludeWorkIQSeed."
  }
  & "$PSScriptRoot\provision_m365_workiq.ps1" `
    -AppName $WorkIqSeederAppName `
    -SecurityGroupObjectId $WorkIqSecurityGroupObjectId
  if ($LASTEXITCODE -ne 0) { throw "Work IQ proof-of-concept content seeding failed with exit code $LASTEXITCODE." }
  Write-Host "    work iq proof-of-concept content seeded" -ForegroundColor Green
}

Write-Host "`nDONE. Live: Foundry IQ ($KbName), Web IQ ($WebKbName), agent $HostedAgentName." -ForegroundColor Green
Write-Host "Set services/api/.env USE_LIVE_FOUNDRY=true, USE_LIVE_WEB=true and run ./scripts/dev.ps1" -ForegroundColor Yellow
