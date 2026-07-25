$scriptPath = Join-Path $PSScriptRoot "..\..\agent\provisioning\live\grant_app_identity_fabric.ps1"
function Require-EnvironmentValue {
  param([Parameter(Mandatory)][string]$Name)
  $value = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ([string]::IsNullOrWhiteSpace($value)) {
    throw "Required azd environment variable '$Name' is not set."
  }
  return $value
}

& $scriptPath `
  -Subscription (Require-EnvironmentValue "AZURE_SUBSCRIPTION_ID") `
  -ResourceGroup (Require-EnvironmentValue "AZURE_RESOURCE_GROUP") `
  -IdentityName (Require-EnvironmentValue "AZURE_MANAGED_IDENTITY_NAME") `
  -FabricWorkspaceId (Require-EnvironmentValue "FABRIC_WORKSPACE_ID") `
  -FabricCapacityResourceGroup (Require-EnvironmentValue "FABRIC_CAPACITY_RESOURCE_GROUP") `
  -FabricCapacityName (Require-EnvironmentValue "FABRIC_CAPACITY_NAME")
