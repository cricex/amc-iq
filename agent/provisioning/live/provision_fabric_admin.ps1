<#
.SYNOPSIS
  Provision a least-privilege Fabric recovery public client and run device-code recovery.
.DESCRIPTION
  Creates or reuses the explicitly named single-tenant public client, with no secrets
  or certificates. The recovery temporarily grants and tenant-admin-consents both:

    - Item.ReadWrite.All       (bootstrap only; required to create a Data Agent)
    - Workspace.Read.All       (bootstrap only; required to find an existing Data Agent)
    - DataAgent.ReadWrite.All  (steady state)
    - Lakehouse.Read.All       (steady state; required to enumerate Lakehouse schema)
    - SQLEndpoint.Read.All     (steady state; required for Lakehouse table metadata)

  After Python recovery, the app registration and tenant-wide delegated grant are
  automatically reconciled back to the three read/configuration scopes above. Failures also
  trigger a best-effort downgrade; reruns temporarily elevate again when agent creation is
  needed.

  The enterprise app requires assignment and permits exactly one assigned principal: the
  supplied object id, or the currently signed-in Azure CLI user.
.PARAMETER ConfirmMutation
  Required explicit acknowledgement that this script mutates Entra and Fabric resources.
.PARAMETER PrincipalObjectId
  Optional user, group, or service-principal object id to assign. Defaults to the signed-in
  Azure CLI user object id.
#>
[CmdletBinding()]
param(
  [switch]$ConfirmMutation,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$AppName,
  [string]$TenantId = "",
  [string]$PrincipalObjectId = "",
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$WorkspaceId,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$EligibilityAgentId,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$LakehouseId,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$GraphModelId,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$GraphAgentName,
  [string]$PythonExecutable = "python",
  [ValidateRange(10, 600)]
  [int]$RequestTimeoutSeconds = 60,
  [ValidateRange(60, 3600)]
  [int]$OperationTimeoutSeconds = 600,
  [ValidateRange(10, 600)]
  [int]$McpTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
# Power BI/Fabric first-party service principal app ID: public Microsoft platform constant.
$PowerBiAppId = "00000009-0000-0000-c000-000000000000"
$DataAgentScope = "DataAgent.ReadWrite.All"
$LakehouseScope = "Lakehouse.Read.All"
$SqlEndpointScope = "SQLEndpoint.Read.All"
$WorkspaceScope = "Workspace.Read.All"
$ItemScope = "Item.ReadWrite.All"
# Entra's public null/default app-role sentinel, not an environment-specific identifier.
$DefaultAppRoleId = "00000000-0000-0000-0000-000000000000"
$GraphBase = "https://graph.microsoft.com/v1.0"
$RecoveryScript = Join-Path $PSScriptRoot "recover_fabric_data_agents.py"
$script:BootstrapManaged = $false
$script:Application = $null
$script:ClientServicePrincipal = $null
$script:ResourceServicePrincipal = $null
$script:Grant = $null
$script:DataAgentScopeId = $null
$script:LakehouseScopeId = $null
$script:SqlEndpointScopeId = $null
$script:WorkspaceScopeId = $null
$script:ItemScopeId = $null

if (-not $ConfirmMutation) {
  throw "Mutations are disabled. Re-run with -ConfirmMutation."
}
if (-not (Test-Path -LiteralPath $RecoveryScript)) {
  throw "Recovery script not found: $RecoveryScript"
}
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
  throw "Azure CLI ('az') is required."
}
if (-not (Get-Command $PythonExecutable -ErrorAction SilentlyContinue)) {
  throw "Python executable '$PythonExecutable' was not found."
}

function ConvertTo-CompactJson {
  param([Parameter(Mandatory)]$Value)
  return ($Value | ConvertTo-Json -Depth 20 -Compress)
}

function Invoke-AzJson {
  param(
    [Parameter(Mandatory)][ValidateSet("GET", "POST", "PATCH", "DELETE")]
    [string]$Method,
    [Parameter(Mandatory)][string]$Uri,
    $Body = $null
  )
  $arguments = @(
    "rest", "--only-show-errors",
    "--method", $Method,
    "--uri", "`"$Uri`"",
    "--output", "json"
  )
  $bodyPath = $null
  if ($null -ne $Body) {
    $bodyPath = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText(
      $bodyPath,
      (ConvertTo-CompactJson $Body),
      [System.Text.UTF8Encoding]::new($false)
    )
    $arguments += @(
      "--headers", "Content-Type=application/json",
      "--body", "@$bodyPath"
    )
  }
  try {
    $output = & az @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
      $detail = (($output | Out-String).Trim())
      throw "Microsoft Graph request failed ($Method $Uri): $detail"
    }
  }
  finally {
    if ($bodyPath -and (Test-Path -LiteralPath $bodyPath)) {
      Remove-Item -LiteralPath $bodyPath -Force
    }
  }
  $text = (($output | Out-String).Trim())
  if (-not $text) {
    return $null
  }
  return $text | ConvertFrom-Json
}

function Get-GraphCollection {
  param([Parameter(Mandatory)][string]$Uri)
  $items = @()
  $next = $Uri
  while ($next) {
    $response = Invoke-AzJson -Method GET -Uri $next
    $items += @($response.value)
    $next = $response.'@odata.nextLink'
  }
  return @($items)
}

function Get-RequiredResourceAccess {
  param([Parameter(Mandatory)][string[]]$ScopeIds)
  return @(
    @{
      resourceAppId = $PowerBiAppId
      resourceAccess = @(
        $ScopeIds | ForEach-Object {
          @{ id = $_; type = "Scope" }
        }
      )
    }
  )
}

function Assert-NoCredentials {
  param([Parameter(Mandatory)]$App)
  if (@($App.passwordCredentials).Count -ne 0 -or @($App.keyCredentials).Count -ne 0) {
    throw "App '$AppName' has a secret or certificate. Refusing to reuse it."
  }
}

function Get-ConfiguredScopeIds {
  param([Parameter(Mandatory)]$App)
  $scopeIds = @()
  foreach ($resource in @($App.requiredResourceAccess)) {
    if ($resource.resourceAppId -ne $PowerBiAppId) {
      throw "App '$AppName' has an extra API resource grant: $($resource.resourceAppId)."
    }
    foreach ($access in @($resource.resourceAccess)) {
      if ($access.type -ne "Scope") {
        throw "App '$AppName' has an application permission. Only delegated scopes are allowed."
      }
      if (
        $access.id -notin @(
          $script:DataAgentScopeId,
          $script:LakehouseScopeId,
          $script:SqlEndpointScopeId,
          $script:WorkspaceScopeId,
          $script:ItemScopeId
        )
      ) {
        throw "App '$AppName' has an unexpected delegated permission id: $($access.id)."
      }
      $scopeIds += [string]$access.id
    }
  }
  if (@($scopeIds | Select-Object -Unique).Count -ne @($scopeIds).Count) {
    throw "App '$AppName' contains duplicate delegated permission entries."
  }
  return @($scopeIds)
}

function Assert-AllowedInitialAppPermissions {
  param([Parameter(Mandatory)]$App)
  $scopeIds = @(Get-ConfiguredScopeIds -App $App)
  $allowedLegacy = @($script:DataAgentScopeId)
  $allowedLakehouseMigration = @(
    $script:DataAgentScopeId,
    $script:LakehouseScopeId
  )
  $allowedFinal = @(
    $script:DataAgentScopeId,
    $script:LakehouseScopeId,
    $script:SqlEndpointScopeId
  )
  $allowedBootstrap = @(
    $script:DataAgentScopeId,
    $script:LakehouseScopeId,
    $script:SqlEndpointScopeId,
    $script:WorkspaceScopeId,
    $script:ItemScopeId
  )
  $isEmpty = $scopeIds.Count -eq 0
  $isLegacy = (
    $scopeIds.Count -eq $allowedLegacy.Count -and
    @($scopeIds | Where-Object { $_ -notin $allowedLegacy }).Count -eq 0
  )
  $isLakehouseMigration = (
    $scopeIds.Count -eq $allowedLakehouseMigration.Count -and
    @(
      $scopeIds |
        Where-Object { $_ -notin $allowedLakehouseMigration }
    ).Count -eq 0
  )
  $isFinal = (
    $scopeIds.Count -eq $allowedFinal.Count -and
    @($scopeIds | Where-Object { $_ -notin $allowedFinal }).Count -eq 0
  )
  $isBootstrap = (
    $scopeIds.Count -eq $allowedBootstrap.Count -and
    @($scopeIds | Where-Object { $_ -notin $allowedBootstrap }).Count -eq 0
  )
  if (
    -not (
      $isEmpty -or
      $isLegacy -or
      $isLakehouseMigration -or
      $isFinal -or
      $isBootstrap
    )
  ) {
    throw "App '$AppName' is not in an allowed migration, steady-state, or bootstrap permission state."
  }
}

function Get-AppRegistration {
  $filter = [uri]::EscapeDataString("displayName eq '$AppName'")
  $select = "id,appId,displayName,signInAudience,isFallbackPublicClient,publicClient,requiredResourceAccess,passwordCredentials,keyCredentials"
  $apps = @(Get-GraphCollection -Uri "$GraphBase/applications?`$filter=$filter&`$select=$select")
  $matches = @($apps | Where-Object { $_.displayName -eq $AppName })
  if ($matches.Count -gt 1) {
    throw "Found $($matches.Count) app registrations named '$AppName'."
  }
  if ($matches.Count -eq 1) {
    return $matches[0]
  }
  Write-Host "Creating single-tenant public client '$AppName' (no credentials)." -ForegroundColor Cyan
  return Invoke-AzJson -Method POST -Uri "$GraphBase/applications" -Body @{
    displayName = $AppName
    signInAudience = "AzureADMyOrg"
    isFallbackPublicClient = $true
    publicClient = @{ redirectUris = @("http://localhost") }
    requiredResourceAccess = @()
    passwordCredentials = @()
    keyCredentials = @()
  }
}

function Get-OrCreateClientServicePrincipal {
  param([Parameter(Mandatory)]$App)
  $filter = [uri]::EscapeDataString("appId eq '$($App.appId)'")
  $servicePrincipals = @(
    Get-GraphCollection -Uri "$GraphBase/servicePrincipals?`$filter=$filter&`$select=id,appId,displayName,appRoleAssignmentRequired"
  )
  if ($servicePrincipals.Count -gt 1) {
    throw "Found multiple service principals for app id $($App.appId)."
  }
  if ($servicePrincipals.Count -eq 1) {
    return $servicePrincipals[0]
  }
  return Invoke-AzJson -Method POST -Uri "$GraphBase/servicePrincipals" -Body @{
    appId = $App.appId
    appRoleAssignmentRequired = $true
  }
}

function Assert-Assignments {
  param(
    [Parameter(Mandatory)]$ClientSp,
    [Parameter(Mandatory)][string]$ExpectedPrincipalId
  )
  $outbound = @(
    Get-GraphCollection -Uri "$GraphBase/servicePrincipals/$($ClientSp.id)/appRoleAssignments?`$select=id,appRoleId,resourceId,principalId"
  )
  if ($outbound.Count -ne 0) {
    throw "Client service principal has application-permission assignments. Refusing extra privilege."
  }

  $inbound = @(
    Get-GraphCollection -Uri "$GraphBase/servicePrincipals/$($ClientSp.id)/appRoleAssignedTo?`$select=id,appRoleId,resourceId,principalId,principalType"
  )
  foreach ($assignment in $inbound) {
    if (
      $assignment.principalId -ne $ExpectedPrincipalId -or
      $assignment.resourceId -ne $ClientSp.id -or
      $assignment.appRoleId -ne $DefaultAppRoleId
    ) {
      throw "Enterprise app has an extra or unexpected principal assignment."
    }
  }
  if ($inbound.Count -gt 1) {
    throw "Enterprise app has duplicate principal assignments."
  }
  return @($inbound)
}

function Ensure-SingleAssignment {
  param(
    [Parameter(Mandatory)]$ClientSp,
    [Parameter(Mandatory)][string]$ExpectedPrincipalId
  )
  $assignments = @(Assert-Assignments -ClientSp $ClientSp -ExpectedPrincipalId $ExpectedPrincipalId)
  if ($assignments.Count -eq 0) {
    Invoke-AzJson -Method POST -Uri "$GraphBase/servicePrincipals/$($ClientSp.id)/appRoleAssignedTo" -Body @{
      principalId = $ExpectedPrincipalId
      resourceId = $ClientSp.id
      appRoleId = $DefaultAppRoleId
    } | Out-Null
  }
  $verified = @(Assert-Assignments -ClientSp $ClientSp -ExpectedPrincipalId $ExpectedPrincipalId)
  if ($verified.Count -ne 1) {
    throw "Failed to establish exactly one enterprise-app assignment."
  }
}

function Get-DelegatedGrant {
  param([Parameter(Mandatory)]$ClientSp)
  $filter = [uri]::EscapeDataString("clientId eq '$($ClientSp.id)'")
  $grants = @(
    Get-GraphCollection -Uri "$GraphBase/oauth2PermissionGrants?`$filter=$filter&`$select=id,clientId,consentType,principalId,resourceId,scope"
  )
  if ($grants.Count -gt 1) {
    throw "Client service principal has extra delegated permission grants."
  }
  if ($grants.Count -eq 0) {
    return $null
  }
  $grant = $grants[0]
  if (
    $grant.resourceId -ne $script:ResourceServicePrincipal.id -or
    $grant.consentType -ne "AllPrincipals" -or
    $null -ne $grant.principalId
  ) {
    throw "Client service principal has an unexpected delegated permission grant."
  }
  $scopeNames = @(
    ([string]$grant.scope -split "\s+") |
      Where-Object { $_ } |
      Select-Object -Unique
  )
  if (
    @(
      $scopeNames |
        Where-Object {
          $_ -notin @(
            $DataAgentScope,
            $LakehouseScope,
            $SqlEndpointScope,
            $WorkspaceScope,
            $ItemScope
          )
        }
    ).Count -ne 0
  ) {
    throw "Delegated grant contains an unexpected scope: $($grant.scope)."
  }
  if ($scopeNames.Count -ne @(([string]$grant.scope -split "\s+") | Where-Object { $_ }).Count) {
    throw "Delegated grant contains duplicate scopes."
  }
  return $grant
}

function Set-AppPermissions {
  param([Parameter(Mandatory)][string[]]$ScopeIds)
  Invoke-AzJson -Method PATCH -Uri "$GraphBase/applications/$($script:Application.id)" -Body @{
    signInAudience = "AzureADMyOrg"
    isFallbackPublicClient = $true
    publicClient = @{ redirectUris = @("http://localhost") }
    requiredResourceAccess = @(Get-RequiredResourceAccess -ScopeIds $ScopeIds)
  } | Out-Null
}

function Set-GrantScopes {
  param([Parameter(Mandatory)][string[]]$ScopeNames)
  $scopeText = ($ScopeNames | Sort-Object) -join " "
  if ($null -eq $script:Grant) {
    $script:Grant = Invoke-AzJson -Method POST -Uri "$GraphBase/oauth2PermissionGrants" -Body @{
      clientId = $script:ClientServicePrincipal.id
      consentType = "AllPrincipals"
      principalId = $null
      resourceId = $script:ResourceServicePrincipal.id
      scope = $scopeText
    }
  } else {
    Invoke-AzJson -Method PATCH -Uri "$GraphBase/oauth2PermissionGrants/$($script:Grant.id)" -Body @{
      scope = $scopeText
    } | Out-Null
    $script:Grant.scope = $scopeText
  }
}

function Test-ExactSet {
  param(
    [Parameter(Mandatory)][string[]]$Actual,
    [Parameter(Mandatory)][string[]]$Expected
  )
  return (
    $Actual.Count -eq $Expected.Count -and
    @($Actual | Where-Object { $_ -notin $Expected }).Count -eq 0
  )
}

function Test-PreExistingBootstrapElevation {
  param(
    [Parameter(Mandatory)]$App,
    $Grant = $null
  )
  $configuredScopeIds = @(Get-ConfiguredScopeIds -App $App)
  $bootstrapScopeIds = @(
    $script:DataAgentScopeId,
    $script:LakehouseScopeId,
    $script:SqlEndpointScopeId,
    $script:WorkspaceScopeId,
    $script:ItemScopeId
  )
  if (Test-ExactSet -Actual $configuredScopeIds -Expected $bootstrapScopeIds) {
    return $true
  }
  if ($null -eq $Grant) {
    return $false
  }
  $grantedScopes = @(([string]$Grant.scope -split "\s+") | Where-Object { $_ })
  return (
    Test-ExactSet -Actual $grantedScopes -Expected @(
      $DataAgentScope,
      $LakehouseScope,
      $SqlEndpointScope,
      $WorkspaceScope,
      $ItemScope
    )
  )
}

function Assert-ExactPermissionState {
  param(
    [Parameter(Mandatory)][string[]]$ScopeIds,
    [Parameter(Mandatory)][string[]]$ScopeNames
  )
  $select = "id,requiredResourceAccess,passwordCredentials,keyCredentials"
  $app = Invoke-AzJson -Method GET -Uri "$GraphBase/applications/$($script:Application.id)?`$select=$select"
  Assert-NoCredentials -App $app
  $configured = @(Get-ConfiguredScopeIds -App $app)
  if (
    $configured.Count -ne $ScopeIds.Count -or
    @($configured | Where-Object { $_ -notin $ScopeIds }).Count -ne 0
  ) {
    throw "App registration permission verification failed."
  }
  $script:Grant = Get-DelegatedGrant -ClientSp $script:ClientServicePrincipal
  if ($null -eq $script:Grant) {
    throw "Tenant-wide delegated consent is missing."
  }
  $granted = @(([string]$script:Grant.scope -split "\s+") | Where-Object { $_ })
  if (
    $granted.Count -ne $ScopeNames.Count -or
    @($granted | Where-Object { $_ -notin $ScopeNames }).Count -ne 0
  ) {
    throw "Tenant-wide delegated consent verification failed."
  }
}

function Set-BootstrapElevation {
  Write-Warning (
    "TEMPORARY BOOTSTRAP ELEVATION: granting Item.ReadWrite.All + Workspace.Read.All + " +
    "DataAgent.ReadWrite.All + Lakehouse.Read.All + SQLEndpoint.Read.All."
  )
  $script:BootstrapManaged = $true
  Set-AppPermissions -ScopeIds @(
    $script:DataAgentScopeId,
    $script:LakehouseScopeId,
    $script:SqlEndpointScopeId,
    $script:WorkspaceScopeId,
    $script:ItemScopeId
  )
  Set-GrantScopes -ScopeNames @(
    $DataAgentScope,
    $LakehouseScope,
    $SqlEndpointScope,
    $WorkspaceScope,
    $ItemScope
  )
  Assert-ExactPermissionState `
    -ScopeIds @(
      $script:DataAgentScopeId,
      $script:LakehouseScopeId,
      $script:SqlEndpointScopeId,
      $script:WorkspaceScopeId,
      $script:ItemScopeId
    ) `
    -ScopeNames @(
      $DataAgentScope,
      $LakehouseScope,
      $SqlEndpointScope,
      $WorkspaceScope,
      $ItemScope
    )
}

function Set-SteadyStatePermissions {
  Write-Host "Removing bootstrap-only Item.ReadWrite.All elevation." -ForegroundColor Cyan
  Set-AppPermissions -ScopeIds @(
    $script:DataAgentScopeId,
    $script:LakehouseScopeId,
    $script:SqlEndpointScopeId
  )
  Set-GrantScopes -ScopeNames @(
    $DataAgentScope,
    $LakehouseScope,
    $SqlEndpointScope
  )
  Assert-ExactPermissionState `
    -ScopeIds @(
      $script:DataAgentScopeId,
      $script:LakehouseScopeId,
      $script:SqlEndpointScopeId
    ) `
    -ScopeNames @($DataAgentScope, $LakehouseScope, $SqlEndpointScope)
  $script:BootstrapManaged = $false
  Write-Host (
    "Steady state verified: DataAgent.ReadWrite.All + Lakehouse.Read.All + " +
    "SQLEndpoint.Read.All."
  ) -ForegroundColor Green
}

try {
  if (-not $TenantId) {
    $TenantId = (& az account show --query tenantId -o tsv 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $TenantId) {
      throw "Unable to resolve the Azure CLI tenant id."
    }
  }
  $activeTenant = (& az account show --query tenantId -o tsv 2>&1 | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $activeTenant -ne $TenantId) {
    throw "Azure CLI is not signed in to requested tenant '$TenantId'."
  }
  if (-not $PrincipalObjectId) {
    $PrincipalObjectId = (
      & az ad signed-in-user show --query id -o tsv 2>&1 | Out-String
    ).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $PrincipalObjectId) {
      throw "Unable to resolve the signed-in admin user object id."
    }
  }

  $resourceFilter = [uri]::EscapeDataString("appId eq '$PowerBiAppId'")
  $resources = @(
    Get-GraphCollection -Uri "$GraphBase/servicePrincipals?`$filter=$resourceFilter&`$select=id,appId,displayName,oauth2PermissionScopes"
  )
  if ($resources.Count -ne 1) {
    throw "Expected exactly one Power BI/Fabric service principal for app id $PowerBiAppId."
  }
  $script:ResourceServicePrincipal = $resources[0]
  $dataScope = @(
    $script:ResourceServicePrincipal.oauth2PermissionScopes |
      Where-Object { $_.value -eq $DataAgentScope -and $_.isEnabled -ne $false }
  )
  $itemPermission = @(
    $script:ResourceServicePrincipal.oauth2PermissionScopes |
      Where-Object { $_.value -eq $ItemScope -and $_.isEnabled -ne $false }
  )
  $lakehousePermission = @(
    $script:ResourceServicePrincipal.oauth2PermissionScopes |
      Where-Object { $_.value -eq $LakehouseScope -and $_.isEnabled -ne $false }
  )
  $sqlEndpointPermission = @(
    $script:ResourceServicePrincipal.oauth2PermissionScopes |
      Where-Object { $_.value -eq $SqlEndpointScope -and $_.isEnabled -ne $false }
  )
  $workspacePermission = @(
    $script:ResourceServicePrincipal.oauth2PermissionScopes |
      Where-Object { $_.value -eq $WorkspaceScope -and $_.isEnabled -ne $false }
  )
  if (
    $dataScope.Count -ne 1 -or
    $lakehousePermission.Count -ne 1 -or
    $sqlEndpointPermission.Count -ne 1 -or
    $workspacePermission.Count -ne 1 -or
    $itemPermission.Count -ne 1
  ) {
    throw "Could not dynamically resolve all required delegated scope ids."
  }
  $script:DataAgentScopeId = [string]$dataScope[0].id
  $script:LakehouseScopeId = [string]$lakehousePermission[0].id
  $script:SqlEndpointScopeId = [string]$sqlEndpointPermission[0].id
  $script:WorkspaceScopeId = [string]$workspacePermission[0].id
  $script:ItemScopeId = [string]$itemPermission[0].id

  $script:Application = Get-AppRegistration
  if ($script:Application.signInAudience -ne "AzureADMyOrg") {
    throw "Existing app '$AppName' is not single-tenant."
  }
  Assert-NoCredentials -App $script:Application
  Assert-AllowedInitialAppPermissions -App $script:Application

  $script:ClientServicePrincipal = Get-OrCreateClientServicePrincipal -App $script:Application
  Invoke-AzJson -Method PATCH -Uri "$GraphBase/servicePrincipals/$($script:ClientServicePrincipal.id)" -Body @{
    appRoleAssignmentRequired = $true
  } | Out-Null
  $script:BootstrapManaged = Test-PreExistingBootstrapElevation -App $script:Application
  $script:Grant = Get-DelegatedGrant -ClientSp $script:ClientServicePrincipal
  $script:BootstrapManaged = (
    $script:BootstrapManaged -or
    (Test-PreExistingBootstrapElevation -App $script:Application -Grant $script:Grant)
  )
  Ensure-SingleAssignment `
    -ClientSp $script:ClientServicePrincipal `
    -ExpectedPrincipalId $PrincipalObjectId
  Set-BootstrapElevation

  Write-Host "Starting device-code Fabric recovery. No access token is logged or persisted." -ForegroundColor Cyan
  & $PythonExecutable $RecoveryScript `
    --tenant-id $TenantId `
    --client-id $script:Application.appId `
    --workspace-id $WorkspaceId `
    --eligibility-agent-id $EligibilityAgentId `
    --lakehouse-id $LakehouseId `
    --graph-model-id $GraphModelId `
    --graph-agent-name $GraphAgentName `
    --confirm-mutations RECOVER `
    --request-timeout $RequestTimeoutSeconds `
    --operation-timeout $OperationTimeoutSeconds `
    --mcp-timeout $McpTimeoutSeconds
  $recoveryExitCode = $LASTEXITCODE
  if ($recoveryExitCode -ne 0) {
    Write-Warning (
      "Fabric recovery failed; bootstrap elevation will be removed before exit."
    )
    throw "Fabric recovery failed with exit code $recoveryExitCode."
  }

  Set-SteadyStatePermissions
  Assert-Assignments `
    -ClientSp $script:ClientServicePrincipal `
    -ExpectedPrincipalId $PrincipalObjectId | Out-Null
  Write-Host "Fabric recovery completed and bootstrap elevation was removed." -ForegroundColor Green
  Write-Host "tenantId=$TenantId clientId=$($script:Application.appId) assignedPrincipal=$PrincipalObjectId"
}
catch {
  $primaryError = $_
  if ($script:BootstrapManaged) {
    try {
      Set-SteadyStatePermissions
    }
    catch {
      throw "$($primaryError.Exception.Message) Best-effort bootstrap downgrade also failed: $($_.Exception.Message)"
    }
  }
  throw $primaryError
}
