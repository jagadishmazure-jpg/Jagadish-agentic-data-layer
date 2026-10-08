// Managed identities and least-privilege role assignments. Role definition ids are Azure built-in roles
// (public and identical in every tenant).
param location string
param suffix string
param storageAccountName string
param keyVaultName string
param searchServiceName string
param tags object

var roles = {
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  storageBlobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  searchIndexDataReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
}

resource lake 'Microsoft.Storage/storageAccounts@2024-01-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' existing = {
  parent: lake
  name: 'default'
}

resource gold 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' existing = {
  parent: blobService
  name: 'gold'
}

resource kv 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource search 'Microsoft.Search/searchServices@2025-05-01' existing = {
  name: searchServiceName
}

resource pipeline 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-adl-pipeline-${suffix}'
  location: location
  tags: tags
}

resource agents 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-adl-agents-${suffix}'
  location: location
  tags: tags
}

// The pipeline writes every layer.
resource pipelineLake 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(lake.id, pipeline.id, roles.storageBlobDataContributor)
  scope: lake
  properties: {
    principalId: pipeline.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
  }
}

// The pipeline reads the pseudonymisation key.
resource pipelineKey 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, pipeline.id, roles.keyVaultSecretsUser)
  scope: kv
  properties: {
    principalId: pipeline.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

// Agents read only the gold container and the search index.
resource agentsGold 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(gold.id, agents.id, roles.storageBlobDataReader)
  scope: gold
  properties: {
    principalId: agents.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataReader)
  }
}

resource agentsSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, agents.id, roles.searchIndexDataReader)
  scope: search
  properties: {
    principalId: agents.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataReader)
  }
}

output agentsClientId string = agents.properties.clientId
output pipelineClientId string = pipeline.properties.clientId
