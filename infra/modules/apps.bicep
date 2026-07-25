param location string
param containerAppsEnvironmentName string
param apiContainerAppName string
param apiContainerImage string
param apiContainerPort int
param staticWebAppName string
param containerRegistryName string
param logAnalyticsCustomerId string
@secure()
param logAnalyticsSharedKey string
param appInsightsConnectionString string
param identityResourceId string
param identityPrincipalId string
param identityClientId string
param searchEndpoint string
param foundryKbName string
param webKbName string
param projectEndpoint string
param fabricWorkspaceId string
param fabricDataAgentId string
param azureSubscriptionId string
param fabricCapacityResourceGroup string
param fabricCapacityName string
param eligibilityEvaluatorAgentName string
param specialistEligibilityAgentName string
param specialistRenalAgentName string
param specialistGenomicsAgentName string
param specialistProtocolAgentName string
param specialistWorkflowAgentName string
param specialistEvidenceAgentName string
param tenantId string
param apiAudience string
param apiRequiredScope string
param workIqClientId string
param workIqClientCertificateSecretName string
param workIqEndpoint string
param workIqScope string
param useLiveWork bool
param useLiveSpecialists bool
param keyVaultUri string
param tags object

// Azure built-in AcrPull role definition ID: a public Microsoft constant, not an environment ID.
var acrPullRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvironmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: containerRegistryName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(containerRegistry.id, identityPrincipalId, acrPullRoleId)
  scope: containerRegistry
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

resource web 'Microsoft.Web/staticSites@2023-12-01' = {
  name: staticWebAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'web'
  })
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Enabled'
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiContainerAppName
  location: location
  tags: union(tags, {
    'azd-service-name': 'api'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: apiContainerPort
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: containerRegistry.properties.loginServer
          identity: identityResourceId
        }
      ]
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
      containers: [
        {
          name: 'api'
          image: apiContainerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'SEARCH_ENDPOINT'
              value: searchEndpoint
            }
            {
              name: 'FOUNDRY_KB_NAME'
              value: foundryKbName
            }
            {
              name: 'WEB_KB_NAME'
              value: webKbName
            }
            {
              name: 'PROJECT_ENDPOINT'
              value: projectEndpoint
            }
            {
              name: 'FABRIC_WORKSPACE_ID'
              value: fabricWorkspaceId
            }
            {
              name: 'FABRIC_DATA_AGENT_ID'
              value: fabricDataAgentId
            }
            {
              name: 'AZURE_SUBSCRIPTION_ID'
              value: azureSubscriptionId
            }
            {
              name: 'FABRIC_CAPACITY_RG'
              value: fabricCapacityResourceGroup
            }
            {
              name: 'FABRIC_CAPACITY_NAME'
              value: fabricCapacityName
            }
            {
              name: 'ELIGIBILITY_EVALUATOR_AGENT'
              value: eligibilityEvaluatorAgentName
            }
            {
              name: 'SPECIALIST_ELIGIBILITY_AGENT'
              value: specialistEligibilityAgentName
            }
            {
              name: 'SPECIALIST_RENAL_AGENT'
              value: specialistRenalAgentName
            }
            {
              name: 'SPECIALIST_GENOMICS_AGENT'
              value: specialistGenomicsAgentName
            }
            {
              name: 'SPECIALIST_PROTOCOL_AGENT'
              value: specialistProtocolAgentName
            }
            {
              name: 'SPECIALIST_WORKFLOW_AGENT'
              value: specialistWorkflowAgentName
            }
            {
              name: 'SPECIALIST_EVIDENCE_AGENT'
              value: specialistEvidenceAgentName
            }
            {
              name: 'CORS_ORIGINS'
              value: 'http://localhost:5173,https://${web.properties.defaultHostname}'
            }
            {
              name: 'AZURE_TENANT_ID'
              value: tenantId
            }
            {
              name: 'API_AUDIENCE'
              value: apiAudience
            }
            {
              name: 'API_REQUIRED_SCOPE'
              value: apiRequiredScope
            }
            {
              name: 'WORK_IQ_CLIENT_ID'
              value: workIqClientId
            }
            {
              name: 'APP_ENVIRONMENT'
              value: 'production'
            }
            {
              name: 'WORK_IQ_KEY_VAULT_URL'
              value: keyVaultUri
            }
            {
              name: 'WORK_IQ_CLIENT_CERTIFICATE_SECRET_NAME'
              value: workIqClientCertificateSecretName
            }
            {
              name: 'WORK_IQ_ENDPOINT'
              value: workIqEndpoint
            }
            {
              name: 'WORK_IQ_SCOPE'
              value: workIqScope
            }
            {
              name: 'USE_LIVE_FOUNDRY'
              value: 'true'
            }
            {
              name: 'USE_LIVE_FABRIC'
              value: 'true'
            }
            {
              name: 'USE_LIVE_WORK'
              value: useLiveWork ? 'true' : 'false'
            }
            {
              name: 'USE_LIVE_WEB'
              value: 'true'
            }
            {
              name: 'USE_MULTI_AGENT'
              value: 'true'
            }
            {
              // The 6-role specialist team drives the Assessment Steps. LIVE narration is enabled for
              // a NARROW set of roles (LIVE_SPECIALIST_ROLES) that reason over the already-established
              // grounded facts; the rest stay grounded. Narrating all 6 live (each re-running KB +
              // Fabric) fans out too far and times out on the single F64 Data Agent.
              name: 'USE_LIVE_SPECIALISTS'
              value: useLiveSpecialists ? 'true' : 'false'
            }
            {
              name: 'LIVE_SPECIALIST_ROLES'
              value: 'eligibility,renal_labs,genomics'
            }
          ]
        }
      ]
    }
  }
  dependsOn: [
    acrPullAssignment
  ]
}

output apiUrl string = 'https://${api.properties.configuration.ingress.fqdn}'
output staticWebAppUrl string = 'https://${web.properties.defaultHostname}'
output staticWebAppId string = web.id
output containerRegistryEndpoint string = containerRegistry.properties.loginServer
