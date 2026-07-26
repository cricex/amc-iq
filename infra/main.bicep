targetScope = 'resourceGroup'

@description('Azure region for new proof-of-concept resources.')
param location string = resourceGroup().location

@description('Tenant for Key Vault and managed-identity auth.')
param tenantId string

@description('API container image. azd deploy replaces this placeholder with the built AMC IQ API image.')
param apiContainerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Port exposed by the API container.')
param apiContainerPort int = 8000

@description('Log Analytics workspace name.')
param logAnalyticsName string

@description('Application Insights component name.')
param appInsightsName string

@description('Container Apps managed environment name.')
param containerAppsEnvironmentName string

@description('Container App for the FastAPI backend.')
param apiContainerAppName string

@description('Static Web App for the React/Vite UI.')
param staticWebAppName string

@description('Azure Container Registry for azd-built API images.')
param containerRegistryName string = take(toLower('amciq${uniqueString(resourceGroup().id)}acr'), 50)

@description('Key Vault for future app secrets. Prefer managed identity and avoid storing keys.')
param keyVaultName string = take(toLower('amciq${uniqueString(resourceGroup().id)}kv'), 24)

@description('User-assigned managed identity shared by the API and role assignments.')
param managedIdentityName string

@description('Existing Azure AI Search resource group. Referenced only; not recreated.')
param existingSearchResourceGroupName string

@description('Existing Azure AI Search service. Referenced only; not recreated.')
param existingSearchServiceName string

@description('Existing Foundry account resource group. Referenced only; not recreated.')
param existingFoundryResourceGroupName string

@description('Existing Foundry account. Referenced only; not recreated.')
param existingFoundryAccountName string

@description('Foundry project name used by live agent tooling.')
param foundryProjectName string

@description('Foundry IQ knowledge base name created by agent/provisioning.')
param foundryKbName string

@description('Web knowledge base name used by live web search.')
param webKbName string

@description('Optional Azure AI Search endpoint override. When empty, derived from existingSearchServiceName.')
param searchEndpoint string = ''

@description('Optional Foundry project endpoint override. When empty, derived from account and project names.')
param projectEndpoint string = ''

@description('Fabric workspace ID used by the API when live Fabric is enabled.')
param fabricWorkspaceId string

@description('Fabric Data Agent ID used by the API when live Fabric is enabled.')
param fabricDataAgentId string

@description('Resource group of the backing Fabric capacity used for the optional status indicator.')
param fabricCapacityResourceGroup string

@description('Backing Fabric capacity resource name used for the optional status indicator.')
param fabricCapacityName string

@description('Foundry eligibility-evaluator agent name used by the production Fabric-native path.')
param eligibilityEvaluatorAgentName string

@description('Optional hosted specialist agent names. Required by the API when useLiveSpecialists is true.')
param specialistEligibilityAgentName string = ''
param specialistRenalAgentName string = ''
param specialistGenomicsAgentName string = ''
param specialistProtocolAgentName string = ''
param specialistWorkflowAgentName string = ''
param specialistEvidenceAgentName string = ''

@description('Application client ID for the confidential amciq-workiq-client registration.')
param workIqClientId string

@description('Application client ID for the operator-provided public SPA registration.')
param webClientId string

@description('Exact audience accepted by the AMC IQ API access-token validator.')
param apiAudience string

@description('Delegated scope claim required by protected AMC IQ API endpoints.')
param apiRequiredScope string = 'access_as_user'

@description('Fully qualified delegated AMC IQ API scope requested by the SPA.')
param webApiScope string

@description('Existing exportable Key Vault certificate secret read by the API managed identity.')
param workIqClientCertificateSecretName string

@description('Work IQ A2A v1.0 gateway endpoint.')
param workIqEndpoint string = 'https://workiq.svc.cloud.microsoft/a2a/'

@description('Work IQ delegated resource scope used for OBO.')
param workIqScope string = 'api://workiq.svc.cloud.microsoft/.default'

@description('Enable live Work IQ integration. Disabled by default to preserve mock fallback behavior.')
param useLiveWork bool = false

@description('Enable live specialist narration. Disable for the faster grounded specialist team.')
param useLiveSpecialists bool = false

@description('Tags applied to every taggable new resource.')
param tags object = {
  project: 'amc-iq'
  scenario: 'foundry-iq'
  env: 'poc'
}

var effectiveSearchEndpoint = empty(searchEndpoint) ? 'https://${existingSearchServiceName}.search.windows.net' : searchEndpoint
var effectiveProjectEndpoint = empty(projectEndpoint) ? 'https://${existingFoundryAccountName}.services.ai.azure.com/api/projects/${foundryProjectName}' : projectEndpoint

module identity 'modules/identity.bicep' = {
  name: 'amciq-identity'
  params: {
    location: location
    managedIdentityName: managedIdentityName
    tags: tags
  }
}

module observability 'modules/observability.bicep' = {
  name: 'amciq-observability'
  params: {
    location: location
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    tags: tags
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'amciq-keyvault'
  params: {
    location: location
    tenantId: tenantId
    keyVaultName: keyVaultName
    apiPrincipalId: identity.outputs.principalId
    roleAssignmentSeed: managedIdentityName
    tags: tags
  }
}

module apps 'modules/apps.bicep' = {
  name: 'amciq-apps'
  params: {
    location: location
    containerAppsEnvironmentName: containerAppsEnvironmentName
    apiContainerAppName: apiContainerAppName
    apiContainerImage: apiContainerImage
    apiContainerPort: apiContainerPort
    staticWebAppName: staticWebAppName
    containerRegistryName: containerRegistryName
    logAnalyticsCustomerId: observability.outputs.logAnalyticsCustomerId
    logAnalyticsSharedKey: observability.outputs.logAnalyticsSharedKey
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    identityResourceId: identity.outputs.identityId
    identityPrincipalId: identity.outputs.principalId
    identityClientId: identity.outputs.clientId
    searchEndpoint: effectiveSearchEndpoint
    foundryKbName: foundryKbName
    webKbName: webKbName
    projectEndpoint: effectiveProjectEndpoint
    fabricWorkspaceId: fabricWorkspaceId
    fabricDataAgentId: fabricDataAgentId
    azureSubscriptionId: subscription().subscriptionId
    fabricCapacityResourceGroup: fabricCapacityResourceGroup
    fabricCapacityName: fabricCapacityName
    eligibilityEvaluatorAgentName: eligibilityEvaluatorAgentName
    specialistEligibilityAgentName: specialistEligibilityAgentName
    specialistRenalAgentName: specialistRenalAgentName
    specialistGenomicsAgentName: specialistGenomicsAgentName
    specialistProtocolAgentName: specialistProtocolAgentName
    specialistWorkflowAgentName: specialistWorkflowAgentName
    specialistEvidenceAgentName: specialistEvidenceAgentName
    tenantId: tenantId
    apiAudience: apiAudience
    apiRequiredScope: apiRequiredScope
    workIqClientId: workIqClientId
    workIqClientCertificateSecretName: workIqClientCertificateSecretName
    workIqEndpoint: workIqEndpoint
    workIqScope: workIqScope
    useLiveWork: useLiveWork
    useLiveSpecialists: useLiveSpecialists
    keyVaultUri: keyVault.outputs.keyVaultUri
    tags: tags
  }
}

module searchRole 'modules/search-role.bicep' = {
  name: 'amciq-api-search-reader'
  scope: resourceGroup(subscription().subscriptionId, existingSearchResourceGroupName)
  params: {
    searchServiceName: existingSearchServiceName
    principalId: identity.outputs.principalId
    roleAssignmentSeed: managedIdentityName
  }
}

module foundryRole 'modules/foundry-role.bicep' = {
  name: 'amciq-api-foundry-user'
  scope: resourceGroup(subscription().subscriptionId, existingFoundryResourceGroupName)
  params: {
    foundryAccountName: existingFoundryAccountName
    principalId: identity.outputs.principalId
    roleAssignmentSeed: managedIdentityName
  }
}

output apiUrl string = apps.outputs.apiUrl
output staticWebAppName string = staticWebAppName
output applicationInsightsConnectionString string = observability.outputs.appInsightsConnectionString
output managedIdentityClientId string = identity.outputs.clientId
output AZURE_MANAGED_IDENTITY_NAME string = managedIdentityName
output keyVaultUri string = keyVault.outputs.keyVaultUri
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = apps.outputs.containerRegistryEndpoint
output API_URL string = apps.outputs.apiUrl
output WEB_URL string = apps.outputs.staticWebAppUrl
output VITE_API_BASE_URL string = apps.outputs.apiUrl
output VITE_ENTRA_TENANT_ID string = tenantId
output VITE_ENTRA_CLIENT_ID string = webClientId
output VITE_API_SCOPE string = webApiScope
