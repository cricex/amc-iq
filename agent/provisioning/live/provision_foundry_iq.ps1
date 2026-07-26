<#
.SYNOPSIS
  Provision the LIVE Foundry IQ layer for the AMC IQ proof-of-concept, VERIFIED working 2026-07-06.
.DESCRIPTION
  Reproduces exactly what was provisioned and tested end-to-end:
    1. Storage account + container, upload data/foundry_docs/
    2. Operator-selected Foundry (AIServices) account + model deployments
    3. Azure AI Search azureBlob knowledge source (auto indexer + embeddings)
    4. Knowledge base (agentic retrieval, answer synthesis, citations)
    5. Smoke test the /retrieve endpoint

  Auth: uses your `az` login (device login supported: `az login --use-device-code`).
  Requires: Owner (or Contributor + role-assignment rights) on the subscription,
            Search Service Contributor + Search Index Data Contributor on the Search service.
  Idempotent-ish: re-running recreates the KS/KB (delete first if they exist).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Subscription,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceGroup,
  [string]$Location        = "eastus2",
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryAccount,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$SearchService,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$SearchRg,
  [string]$StorageAccount  = "",                      # blank => create a new amciqdocs<rand>
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Container,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ChatDeployment,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ChatModel,
  [string]$ChatVersion     = "2026-03-05",
  [string]$EmbedDeployment = "text-embedding-3-large",
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$KsName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$KbName,
  [string]$ApiVersion      = "2026-05-01-preview"
)
$ErrorActionPreference = "Stop"
az account set --subscription $Subscription | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to select Azure subscription $Subscription."
}
$root    = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$docsDir = Join-Path $root "data\foundry_docs"
$tags    = @("project=amc-iq","scenario=foundry-iq","env=poc")

function Search-Headers {
  $t = az account get-access-token --scope "https://search.azure.com/.default" --query accessToken -o tsv
  return @{ Authorization = "Bearer $t"; "Content-Type" = "application/json" }
}

Write-Host "1) Storage + upload docs" -ForegroundColor Cyan
if (-not $StorageAccount) {
  $sfx = -join ((48..57)+(97..102) | Get-Random -Count 6 | ForEach-Object {[char]$_})
  $StorageAccount = "amciqdocs$sfx"
  az storage account create -n $StorageAccount -g $ResourceGroup -l $Location --sku Standard_LRS --kind StorageV2 `
     --allow-blob-public-access false --min-tls-version TLS1_2 --tags $tags | Out-Null
}
$key  = az storage account keys list -n $StorageAccount -g $ResourceGroup --query "[0].value" -o tsv
$conn = az storage account show-connection-string -n $StorageAccount -g $ResourceGroup --query connectionString -o tsv
az storage container create -n $Container --account-name $StorageAccount --account-key $key --only-show-errors | Out-Null
Get-ChildItem $docsDir -Filter *.md | ForEach-Object {
  az storage blob upload --account-name $StorageAccount --account-key $key -c $Container -f $_.FullName -n $_.Name --overwrite --only-show-errors | Out-Null
}
Write-Host "   storage=$StorageAccount container=$Container docs uploaded" -ForegroundColor Green

Write-Host "2) Foundry account + model deployments" -ForegroundColor Cyan
if (-not (az cognitiveservices account show -n $FoundryAccount -g $ResourceGroup 2>$null)) {
  az cognitiveservices account create -n $FoundryAccount -g $ResourceGroup -l $Location --kind AIServices --sku S0 `
     --custom-domain $FoundryAccount --assign-identity --yes --tags $tags | Out-Null
}
$fUri = "https://$FoundryAccount.openai.azure.com"
$fKey = az cognitiveservices account keys list -n $FoundryAccount -g $ResourceGroup --query key1 -o tsv
foreach ($d in @(@{n=$EmbedDeployment;m="text-embedding-3-large";v="1";sku="Standard";cap=120},
                 @{n=$ChatDeployment;m=$ChatModel;v=$ChatVersion;sku="GlobalStandard";cap=50})) {
  if (-not (az cognitiveservices account deployment show -n $FoundryAccount -g $ResourceGroup --deployment-name $d.n 2>$null)) {
    az cognitiveservices account deployment create -n $FoundryAccount -g $ResourceGroup --deployment-name $d.n `
       --model-name $d.m --model-version $d.v --model-format OpenAI --sku-name $d.sku --sku-capacity $d.cap | Out-Null
  }
}
Write-Host "   foundry=$FoundryAccount chat=$ChatDeployment embed=$EmbedDeployment" -ForegroundColor Green

Write-Host "3) Knowledge source (azureBlob)" -ForegroundColor Cyan
$hdr = Search-Headers
$ks = @{
  name = $KsName; kind = "azureBlob"
  description = "AMC IQ synthetic oncology institutional knowledge: trial protocols/amendment, IRB/consent, SOPs, escalation rules, SOC pathway, and synthetic patient notes/pathology/genomics/MTB summaries."
  azureBlobParameters = @{
    connectionString = $conn; containerName = $Container
    ingestionParameters = @{
      contentExtractionMode = "minimal"
      embeddingModel = @{ kind="azureOpenAI"; azureOpenAIParameters=@{ resourceUri=$fUri; deploymentId=$EmbedDeployment; apiKey=$fKey; modelName="text-embedding-3-large" } }
      chatCompletionModel = @{ kind="azureOpenAI"; azureOpenAIParameters=@{ resourceUri=$fUri; deploymentId=$ChatDeployment; apiKey=$fKey; modelName=$ChatModel } }
    }
  }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Put -Headers $hdr -Uri "https://$SearchService.search.windows.net/knowledgesources/$KsName`?api-version=$ApiVersion" -Body $ks | Out-Null
Write-Host "   ks=$KsName created (auto indexer/skillset/index)" -ForegroundColor Green

Write-Host "4) Knowledge base (agentic retrieval)" -ForegroundColor Cyan
$kb = @{
  name = $KbName
  description = "AMC IQ precision-oncology institutional knowledge base grounding a clinical-trial eligibility assistant."
  retrievalInstructions = "Institutional clinical-trial knowledge: eligibility inclusion/exclusion (renal thresholds like CrCl, prior-therapy exclusions), protocol amendments, IRB/consent, SOPs, site escalation, and synthetic patient notes/pathology/genomics. Preserve trial IDs (e.g. NCT99004324) and patient IDs (e.g. PT-1042)."
  answerInstructions = "Answer concisely, grounded ONLY in retrieved content, with citations. Surface eligibility criteria precisely, especially borderline renal thresholds and ambiguous prior-therapy exclusions; flag values near a threshold or requiring PI confirmation / repeat lab. Never give a definitive medical decision; recommend human review. If content is insufficient, say so."
  outputMode = "answerSynthesis"
  knowledgeSources = @(@{ name = $KsName })
  models = @(@{ kind="azureOpenAI"; azureOpenAIParameters=@{ resourceUri=$fUri; deploymentId=$ChatDeployment; apiKey=$fKey; modelName=$ChatModel } })
  retrievalReasoningEffort = @{ kind = "medium" }
} | ConvertTo-Json -Depth 10
Invoke-RestMethod -Method Put -Headers $hdr -Uri "https://$SearchService.search.windows.net/knowledgebases/$KbName`?api-version=$ApiVersion" -Body $kb | Out-Null
Write-Host "   kb=$KbName created" -ForegroundColor Green
Write-Host "   MCP endpoint: https://$SearchService.search.windows.net/knowledgebases/$KbName/mcp?api-version=$ApiVersion" -ForegroundColor Yellow

Write-Host "5) Smoke test /retrieve (wait for indexer)" -ForegroundColor Cyan
Start-Sleep -Seconds 20
$q = "For trial NCT99004324, what is the CrCl eligibility threshold and how is the prior platinum exclusion handled?"
$body = @{ messages = @(@{ role="user"; content=@(@{ type="text"; text=$q }) }) } | ConvertTo-Json -Depth 8
$hdr = Search-Headers
$r = Invoke-RestMethod -Method Post -Headers $hdr -Uri "https://$SearchService.search.windows.net/knowledgebases/$KbName/retrieve?api-version=$ApiVersion" -Body $body
Write-Host "   references: $(($r.references|Measure-Object).Count)" -ForegroundColor Green
($r.response | ForEach-Object { $_.content } | ForEach-Object { $_.text }) -join "`n"
