targetScope = 'resourceGroup'

param searchServiceName string
param principalId string
param roleAssignmentSeed string

// Azure built-in Search Index Data Reader role definition ID (public Microsoft constant).
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'

resource search 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchServiceName
}

resource assignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, roleAssignmentSeed, searchIndexDataReaderRoleId)
  scope: search
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
  }
}
