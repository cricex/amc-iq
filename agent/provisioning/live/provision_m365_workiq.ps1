<#
.SYNOPSIS
  Provision a dedicated device-code public client and seed synthetic Microsoft 365 Work IQ proof-of-concept
  content, idempotently and least privilege.
.DESCRIPTION
  Prepares an explicitly named single-tenant public-client (device-code) app registration with no
  secrets and no certificates, requests only the low-privilege delegated Microsoft Graph scopes the
  seeder needs, restricts the app to the `amciq-workiq-users` security group, admin-consents the
  scopes, then runs the Python device-code seeder to write synthetic care-team evidence into the
  signed-in user's Microsoft 365 mailbox, calendar, To Do, root SharePoint site, and Teams
  chat-with-yourself thread. It then verifies the seeded facts through the GA Work IQ A2A endpoint.
  The separate Microsoft 365 Copilot Retrieval API probes remain non-blocking diagnostics for
  semantic indexing of the SharePoint transcripts and productivity files.

  Requested delegated scopes (nothing else): User.Read, Mail.ReadBasic, Mail.Send,
  Calendars.ReadWrite, Tasks.ReadWrite, Files.ReadWrite.All, Files.Read.All, Sites.Read.All,
  Chat.Read, ChatMessage.Send, plus WorkIQAgent.Ask on the Work IQ API. Files.ReadWrite.All is
  required for the shared SharePoint document
  library used by this tenant; Files.Read.All and Sites.Read.All are required by the Copilot
  Retrieval API. Chat.Read is used only to make self-chat writes idempotent. No application
  permissions. Graph scope IDs are resolved dynamically from the Microsoft Graph service principal
  by scope value, never hardcoded.

  Safe to re-run: every step probes then creates or patches. It never deletes an app, secret, group
  membership, or seeded item, and it never prints an access token.

  Auth: your `az` login (device login supported: `az login --use-device-code`). Requires permission
  to create app registrations and service principals, assign app roles, and grant admin consent
  (for example Application Administrator or Cloud Application Administrator, plus the ability to
  consent to these delegated scopes).

  Prereqs: az CLI, and the repo venv at `.venv` with `services/api/requirements.txt` installed
  (msal and httpx are already listed there).
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$AppName,
  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$SecurityGroupObjectId,
  [string]$Tenant,                 # defaults to the current `az account` tenant
  [string]$DataDir,                # defaults to <repo>/data/work
  [switch]$SkipSeeder              # provision the app only; do not run the device-code seeder
)
$ErrorActionPreference = "Stop"

# Microsoft Graph and Work IQ first-party app IDs: public Microsoft platform constants.
$GraphAppId = "00000003-0000-0000-c000-000000000000"
$WorkIqAppId = "fdcc1f02-fc51-4226-8753-f668596af7f7"
$WorkIqScopeValue = "WorkIQAgent.Ask"
$GraphBase  = "https://graph.microsoft.com/v1.0"
$DefaultAppRoleId = "00000000-0000-0000-0000-000000000000"  # default "user/group access" role
$ScopeValues = @(
  "User.Read",
  "Mail.ReadBasic",
  "Mail.Send",
  "Calendars.ReadWrite",
  "Tasks.ReadWrite",
  "Files.ReadWrite.All",
  "Files.Read.All",
  "Sites.Read.All",
  "Chat.Read",
  "ChatMessage.Send"
)

function Invoke-Graph {
  <# Thin wrapper over `az rest` for Microsoft Graph. Body is passed as a temp file to avoid shell
     quoting issues. Bounded retry with backoff on throttling (429) and 5xx; other failures throw
     immediately. Returns parsed JSON (or $null for empty responses). #>
  param(
    [Parameter(Mandatory)] [string]$Method,
    [Parameter(Mandatory)] [string]$Url,
    [string]$BodyJson,
    [int]$MaxAttempts = 4
  )
  $restArgs = @("rest", "--method", $Method, "--url", $Url)
  $tmp = $null
  if ($BodyJson) {
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp.FullName -Value $BodyJson -Encoding utf8
    $restArgs += @("--headers", "Content-Type=application/json", "--body", "@$($tmp.FullName)")
  }
  try {
    $out = $null
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
      $out = az @restArgs 2>&1
      if ($LASTEXITCODE -eq 0) { break }
      $text = "$out"
      $retryable = ($text -match '\b(429|500|502|503|504)\b') -or ($text -match 'TooManyRequests|ServiceUnavailable|throttl|temporarily')
      if ($retryable -and $attempt -lt $MaxAttempts) {
        Start-Sleep -Seconds ([int][math]::Min([math]::Pow(2, $attempt - 1), 30))
        continue
      }
      throw "Graph $Method $Url failed: $out"
    }
  } finally {
    if ($tmp) { Remove-Item $tmp.FullName -ErrorAction SilentlyContinue }
  }
  if ($out) { return ($out | ConvertFrom-Json) }
  return $null
}

# ------------------------------------------------------------------------------------------------
# 0. Context
# ------------------------------------------------------------------------------------------------
$account = az account show -o json 2>$null | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $account) { throw "Not signed in. Run: az login --use-device-code" }
if (-not $Tenant) { $Tenant = $account.tenantId }
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$py   = Join-Path $repo ".venv\Scripts\python.exe"
if (-not $DataDir) { $DataDir = Join-Path $repo "data\work" }

# ------------------------------------------------------------------------------------------------
# 1. Resolve Graph delegated scope IDs dynamically (never hardcoded)
# ------------------------------------------------------------------------------------------------
Write-Host "[1] Resolving Microsoft Graph delegated scope IDs by value" -ForegroundColor Cyan
$graphSp = az ad sp show --id $GraphAppId -o json | ConvertFrom-Json
if (-not $graphSp) { throw "Could not read the Microsoft Graph service principal." }
$graphSpId = $graphSp.id
$scopeMap = @{}
foreach ($scope in $graphSp.oauth2PermissionScopes) { $scopeMap[$scope.value] = $scope.id }
$graphResourceAccess = foreach ($value in $ScopeValues) {
  if (-not $scopeMap.ContainsKey($value)) { throw "Graph delegated scope '$value' was not found on the Graph service principal." }
  @{ id = $scopeMap[$value]; type = "Scope" }
}
$workIqSp = az ad sp show --id $WorkIqAppId -o json | ConvertFrom-Json
if (-not $workIqSp) { throw "Could not read the Work IQ service principal." }
$workIqScope = @($workIqSp.oauth2PermissionScopes) |
  Where-Object { $_.value -eq $WorkIqScopeValue } |
  Select-Object -First 1
if (-not $workIqScope) {
  throw "Work IQ delegated scope '$WorkIqScopeValue' was not found on the Work IQ service principal."
}
$requiredResourceAccessJson = @(
  @{
    resourceAppId = $GraphAppId
    resourceAccess = @($graphResourceAccess)
  },
  @{
    resourceAppId = $WorkIqAppId
    resourceAccess = @(@{ id = $workIqScope.id; type = "Scope" })
  }
) | ConvertTo-Json -Depth 8 -Compress
Write-Host "    resolved $($ScopeValues.Count) Graph scopes + $WorkIqScopeValue" -ForegroundColor Green

# ------------------------------------------------------------------------------------------------
# 2. Find or create the public-client app registration (no secrets, no certificates)
# ------------------------------------------------------------------------------------------------
Write-Host "[2] App registration $AppName (public client, device code, single tenant)" -ForegroundColor Cyan
$existingApps = az ad app list --display-name $AppName -o json | ConvertFrom-Json
$app = @($existingApps) | Where-Object { $_.displayName -eq $AppName } | Select-Object -First 1
if (-not $app) {
  $app = az ad app create --display-name $AppName --sign-in-audience AzureADMyOrg --is-fallback-public-client true -o json | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0 -or -not $app) { throw "Failed to create app registration $AppName." }
}
$appId       = $app.appId
$appObjectId = $app.id

if (@($app.passwordCredentials).Count -gt 0 -or @($app.keyCredentials).Count -gt 0) {
  throw "$AppName has unexpected secrets or certificates. Remove them before using the public-client seeder."
}

# Enforce desired state each run: public client on, only the delegated allowlist requested.
$appPatchJson = '{"isFallbackPublicClient":true,"requiredResourceAccess":' + $requiredResourceAccessJson + '}'
Invoke-Graph -Method PATCH -Url "$GraphBase/applications/$appObjectId" -BodyJson $appPatchJson | Out-Null
Write-Host "    appId=$appId (public client, delegated scopes set)" -ForegroundColor Green

# ------------------------------------------------------------------------------------------------
# 3. Find or create the service principal
# ------------------------------------------------------------------------------------------------
Write-Host "[3] Service principal" -ForegroundColor Cyan
$spJson = az ad sp show --id $appId -o json 2>$null
if ($LASTEXITCODE -ne 0 -or -not $spJson) {
  $sp = $null
  foreach ($attempt in 1..3) {
    $sp = az ad sp create --id $appId -o json 2>$null | ConvertFrom-Json
    if ($LASTEXITCODE -eq 0 -and $sp) { break }
    Start-Sleep -Seconds 5   # allow app replication before the SP create
  }
  if (-not $sp) { throw "Failed to create the service principal for appId $appId." }
} else {
  $sp = $spJson | ConvertFrom-Json
}
$spObjectId = $sp.id
Write-Host "    spObjectId=$spObjectId" -ForegroundColor Green

# ------------------------------------------------------------------------------------------------
# 4. Restrict the app: appRoleAssignmentRequired = true (idempotent)
# ------------------------------------------------------------------------------------------------
Write-Host "[4] Require app-role assignment (restrict sign-in to assigned principals)" -ForegroundColor Cyan
Invoke-Graph -Method PATCH -Url "$GraphBase/servicePrincipals/$spObjectId" -BodyJson '{"appRoleAssignmentRequired":true}' | Out-Null
Write-Host "    appRoleAssignmentRequired=true" -ForegroundColor Green

# ------------------------------------------------------------------------------------------------
# 5. Assign only the amciq-workiq-users group, default access role (idempotent)
# ------------------------------------------------------------------------------------------------
Write-Host "[5] Assign security group $SecurityGroupObjectId (default access role)" -ForegroundColor Cyan
$assignments = Invoke-Graph -Method GET -Url "$GraphBase/servicePrincipals/$spObjectId/appRoleAssignedTo"
$unexpectedAssignments = @($assignments.value) | Where-Object {
  $_.principalId -ne $SecurityGroupObjectId -or
  $_.appRoleId -ne $DefaultAppRoleId -or
  $_.resourceId -ne $spObjectId
}
if ($unexpectedAssignments) {
  throw "$AppName has unexpected user, group, or app-role assignments. Remove them before running the seeder."
}
$existingAssignment = @($assignments.value) | Where-Object { $_.principalId -eq $SecurityGroupObjectId } | Select-Object -First 1
if (-not $existingAssignment) {
  $assignJson = "{`"principalId`":`"$SecurityGroupObjectId`",`"resourceId`":`"$spObjectId`",`"appRoleId`":`"$DefaultAppRoleId`"}"
  Invoke-Graph -Method POST -Url "$GraphBase/servicePrincipals/$spObjectId/appRoleAssignedTo" -BodyJson $assignJson | Out-Null
  Write-Host "    group assigned" -ForegroundColor Green
} else {
  Write-Host "    group already assigned" -ForegroundColor DarkGray
}

# ------------------------------------------------------------------------------------------------
# 6. Grant admin consent for the delegated scopes (idempotent; no duplicate grant)
# ------------------------------------------------------------------------------------------------
Write-Host "[6] Admin consent for delegated scopes" -ForegroundColor Cyan
$scopeString = ($ScopeValues -join " ")
$grants = Invoke-Graph -Method GET -Url "$GraphBase/servicePrincipals/$spObjectId/oauth2PermissionGrants"
$allowedResourceIds = @($graphSpId, $workIqSp.id)
$unexpectedGrants = @($grants.value) | Where-Object {
  $_.resourceId -notin $allowedResourceIds -or $_.consentType -ne "AllPrincipals"
}
if ($unexpectedGrants) {
  throw "$AppName has unexpected delegated grants or per-user consent."
}
$appRoleAssignments = Invoke-Graph -Method GET -Url "$GraphBase/servicePrincipals/$spObjectId/appRoleAssignments"
if (@($appRoleAssignments.value).Count -gt 0) {
  throw "$AppName has unexpected application permissions. Remove them before running the seeder."
}

function Ensure-DelegatedGrant {
  param(
    [Parameter(Mandatory)] [string]$ResourceId,
    [Parameter(Mandatory)] [string]$Scopes,
    [Parameter(Mandatory)] [string]$Label
  )
  $resourceGrants = @($grants.value) | Where-Object { $_.resourceId -eq $ResourceId }
  if (@($resourceGrants).Count -gt 1) {
    throw "$AppName has duplicate $Label delegated grants."
  }
  $existingGrant = $resourceGrants | Select-Object -First 1
  if (-not $existingGrant) {
    $grantJson = "{`"clientId`":`"$spObjectId`",`"consentType`":`"AllPrincipals`",`"resourceId`":`"$ResourceId`",`"scope`":`"$Scopes`"}"
    Invoke-Graph -Method POST -Url "$GraphBase/oauth2PermissionGrants" -BodyJson $grantJson | Out-Null
    Write-Host "    $Label consent granted" -ForegroundColor Green
  } else {
    $have = @($existingGrant.scope -split '\s+' | Where-Object { $_ })
    $desired = @($Scopes -split '\s+' | Where-Object { $_ } | Select-Object -Unique)
    $differs = @(Compare-Object -ReferenceObject $desired -DifferenceObject $have).Count -gt 0
    if ($differs) {
      Invoke-Graph -Method PATCH -Url "$GraphBase/oauth2PermissionGrants/$($existingGrant.id)" -BodyJson "{`"scope`":`"$Scopes`"}" | Out-Null
      Write-Host "    $Label consent reconciled" -ForegroundColor Green
    } else {
      Write-Host "    $Label consent already present" -ForegroundColor DarkGray
    }
  }
}
Ensure-DelegatedGrant -ResourceId $graphSpId -Scopes $scopeString -Label "Microsoft Graph"
Ensure-DelegatedGrant -ResourceId $workIqSp.id -Scopes $WorkIqScopeValue -Label "Work IQ"

# ------------------------------------------------------------------------------------------------
# 7. Run the seeder (DPAPI-encrypted MSAL cache; device code only when interaction is required)
# ------------------------------------------------------------------------------------------------
if ($SkipSeeder) {
  Write-Host "[7] Seeder skipped (-SkipSeeder). App is provisioned." -ForegroundColor Yellow
} else {
  if (-not (Test-Path $py)) { throw "Repo venv Python not found at $py. Create .venv and install services\api\requirements.txt." }
  Write-Host "[7] Seeding Microsoft 365 proof-of-concept content (device code only if the encrypted cache cannot refresh)" -ForegroundColor Cyan
  & $py "$PSScriptRoot\seed_m365_workiq.py" --tenant-id $Tenant --client-id $appId --data-dir $DataDir
  if ($LASTEXITCODE -ne 0) { throw "Seeder failed with exit code $LASTEXITCODE." }
  Write-Host "    seed complete" -ForegroundColor Green
}

Write-Host "`nDONE. App $AppName (appId=$appId) provisioned; tenant=$Tenant." -ForegroundColor Green
Write-Host "Non-secret summary: appId=$appId spObjectId=$spObjectId group=$SecurityGroupObjectId graphScopes='$scopeString' workIqScope='$WorkIqScopeValue'" -ForegroundColor Gray
