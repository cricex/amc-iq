<#
.SYNOPSIS
  Grant the deployed app's managed identity access to Fabric and its backing capacity.
.DESCRIPTION
  The API Container App runs as the operator-selected user-assigned managed identity created by the
  infrastructure template. For live Fabric IQ (`USE_LIVE_FABRIC=true`) the app calls the Fabric Data Agent
  MCP endpoint with that identity's token, so the identity must be a member of the Fabric workspace.
  The UI also reads the backing Fabric capacity state through ARM, so the identity needs Reader on
  that capacity resource. The workspace step is a Fabric REST call (not ARM), so this script runs as
  an azd `postprovision` hook (see infra/azure.yaml) or standalone after `azd up`.

  Idempotent: re-running preserves the existing workspace membership and Reader assignment.

  Auth: your `az` login. Requires Admin/Member on the Fabric workspace and permission to create role
  assignments on the Fabric capacity.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$IdentityName,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$ResourceGroup,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricWorkspaceId,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$Subscription,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricCapacityResourceGroup,
  [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$FabricCapacityName
)
$ErrorActionPreference = "Stop"

az account set --subscription $Subscription | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to select Azure subscription $Subscription."
}
$principalId = az identity show -n $IdentityName -g $ResourceGroup --query principalId -o tsv 2>$null
if ($LASTEXITCODE -ne 0 -or -not $principalId) {
  throw "Managed identity $IdentityName was not found in resource group $ResourceGroup."
}
$fabTok = az account get-access-token --scope "https://api.fabric.microsoft.com/.default" --query accessToken -o tsv
if ($LASTEXITCODE -ne 0 -or -not $fabTok) {
  throw "Failed to acquire a Fabric API token."
}
$roleAssignmentsUri = "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/roleAssignments"
$roleAssignments = Invoke-RestMethod -Headers @{ Authorization = "Bearer $fabTok" } -Uri $roleAssignmentsUri
$existingMembership = @($roleAssignments.value) | Where-Object { $_.principal.id -eq $principalId }
$body = @{ principal = @{ id = $principalId; type = "ServicePrincipal" }; role = "Member" } | ConvertTo-Json
if (-not $existingMembership) {
  Invoke-RestMethod -Method Post -Headers @{ Authorization = "Bearer $fabTok"; "Content-Type" = "application/json" } `
    -Uri "https://api.fabric.microsoft.com/v1/workspaces/$FabricWorkspaceId/roleAssignments" -Body $body | Out-Null
  Write-Host "  granted $IdentityName ($principalId) Member on Fabric workspace $FabricWorkspaceId" -ForegroundColor Green
} else {
  Write-Host "  $IdentityName already has Fabric workspace access" -ForegroundColor DarkGray
}

$capacityScope = (
  "/subscriptions/$Subscription/resourceGroups/$FabricCapacityResourceGroup" +
  "/providers/Microsoft.Fabric/capacities/$FabricCapacityName"
)
$readerAssignment = az role assignment list --assignee-object-id $principalId --scope $capacityScope `
  --role Reader --query "[0].id" -o tsv
if ($LASTEXITCODE -ne 0) {
  throw "Failed to inspect Reader access on Fabric capacity $FabricCapacityName."
}
if (-not $readerAssignment) {
  az role assignment create --assignee-object-id $principalId --assignee-principal-type ServicePrincipal `
    --role Reader --scope $capacityScope | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to grant Reader on Fabric capacity $FabricCapacityName."
  }
  Write-Host "  granted $IdentityName ($principalId) Reader on Fabric capacity $FabricCapacityName" -ForegroundColor Green
} else {
  Write-Host "  $IdentityName already has Reader on Fabric capacity $FabricCapacityName" -ForegroundColor DarkGray
}
