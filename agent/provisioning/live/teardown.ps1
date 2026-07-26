<#
.SYNOPSIS
  Tear down the AMC IQ live environment (for a clean rebuild). Idempotent.
.DESCRIPTION
  Removes the AMC IQ proof-of-concept resources:
    - Azure AI Search (operator-selected shared service; only explicitly named KBs/KS/index are deleted)
    - Fabric items in the workspace (Data Agent + Lakehouse), SHARED workspace, only amciq_* items
    - The explicitly selected resource group contents created by the proof-of-concept

  Auth: your `az` login. Use -DeleteResourceGroup to remove the explicitly selected resource group.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Subscription,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceGroup,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryAccount,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryProject,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$SearchService,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricWorkspaceId,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryKbName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$WebKbName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryKnowledgeSourceName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$WebKnowledgeSourceName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FoundryIndexName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricItemPrefix,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$StorageAccountPrefix,
  [string]$ApiVersion     = "2026-05-01-preview",
  [switch]$DeleteFoundryAccount,
  [switch]$DeleteResourceGroup
)
$ErrorActionPreference = "Continue"
az account set --subscription $Subscription | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to select Azure subscription $Subscription."
}
$searchEndpoint = "https://$SearchService.search.windows.net"

Write-Host "[1] Delete Search KBs / KS / index (amciq-*)" -ForegroundColor Cyan
$sh = @{ Authorization = "Bearer $(az account get-access-token --scope 'https://search.azure.com/.default' --query accessToken -o tsv)" }
foreach ($kb in @($FoundryKbName, $WebKbName)) {
  Invoke-RestMethod -Method Delete -Headers $sh -Uri "$searchEndpoint/knowledgebases/$kb`?api-version=$ApiVersion" -ErrorAction SilentlyContinue | Out-Null
  Write-Host "    deleted kb $kb" -ForegroundColor DarkGray
}
Start-Sleep 3
foreach ($ks in @($FoundryKnowledgeSourceName, $WebKnowledgeSourceName)) {
  Invoke-RestMethod -Method Delete -Headers $sh -Uri "$searchEndpoint/knowledgesources/$ks`?api-version=$ApiVersion" -ErrorAction SilentlyContinue | Out-Null
  Write-Host "    deleted ks $ks" -ForegroundColor DarkGray
}
Invoke-RestMethod -Method Delete -Headers $sh -Uri "$searchEndpoint/indexes/$FoundryIndexName`?api-version=$ApiVersion" -ErrorAction SilentlyContinue | Out-Null

Write-Host "[2] Delete Fabric items (amciq_*)" -ForegroundColor Cyan
$ft = az account get-access-token --scope "https://api.fabric.microsoft.com/.default" --query accessToken -o tsv
$fh = @{ Authorization = "Bearer $ft" }
try {
  $items = (Invoke-RestMethod -Headers $fh -Uri "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/items").value |
           Where-Object { $_.displayName -like "$FabricItemPrefix*" }
  foreach ($it in $items) {
    Invoke-RestMethod -Method Delete -Headers $fh -Uri "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/items/$($it.id)" -ErrorAction SilentlyContinue | Out-Null
    Write-Host "    deleted $($it.type): $($it.displayName)" -ForegroundColor DarkGray
  }
} catch { Write-Host "    (fabric list failed: $_)" -ForegroundColor DarkYellow }

Write-Host "[3] Foundry account/project + storage" -ForegroundColor Cyan
if ($DeleteResourceGroup) {
  Write-Host "    deleting resource group $ResourceGroup (Foundry + storage) ..." -ForegroundColor Yellow
  az group delete -n $ResourceGroup --yes --no-wait | Out-Null
} else {
  # Delete the explicitly named proof-of-concept project and storage while preserving a potentially shared
  # Foundry account unless the operator separately confirms account deletion.
  az cognitiveservices account project delete -n $FoundryAccount -g $ResourceGroup --project-name $FoundryProject 2>$null | Out-Null
  if ($DeleteFoundryAccount) {
    az cognitiveservices account delete -n $FoundryAccount -g $ResourceGroup 2>$null | Out-Null
  }
  $stg = az storage account list -g $ResourceGroup --query "[?starts_with(name,'$StorageAccountPrefix')].name | [0]" -o tsv
  if ($stg) { az storage account delete -n $stg -g $ResourceGroup --yes 2>$null | Out-Null; Write-Host "    deleted storage $stg" -ForegroundColor DarkGray }
}
Write-Host "`nTeardown complete. Re-provision with provision_all.ps1." -ForegroundColor Green
