// The lake (ADLS Gen2 with one container per layer plus audit), Key Vault for the pseudonymisation key,
// Azure AI Search for the knowledge layer and Log Analytics with audit diagnostics. Entra ID auth only.
param location string
param suffix string
@minLength(3)
@maxLength(20)
param compact string
param environment string
param privateNetworking bool
param tags object

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-adl-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: 1 }
    features: { disableLocalAuth: true }
  }
}

resource lake 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: 'st${compact}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: environment == 'prod' ? 'Standard_ZRS' : 'Standard_LRS' }
  identity: { type: 'SystemAssigned' }
  properties: {
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    allowBlobPublicAccess: false
    isLocalUserEnabled: false
    isSftpEnabled: false
    publicNetworkAccess: privateNetworking ? 'Disabled' : 'Enabled'
    encryption: {
      requireInfrastructureEncryption: true
      keySource: 'Microsoft.Storage'
      services: {
        blob: { enabled: true }
        file: { enabled: true }
      }
    }
    networkAcls: {
      defaultAction: privateNetworking ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: lake
  name: 'default'
  properties: {
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = [
  for name in ['bronze', 'silver', 'gold', 'audit']: {
    parent: blobService
    name: name
    properties: { publicAccess: 'None' }
  }
]

resource kv 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: 'kv-${compact}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 30
    publicNetworkAccess: privateNetworking ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: privateNetworking ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource search 'Microsoft.Search/searchServices@2025-05-01' = {
  name: 'srch-adl-${suffix}'
  location: location
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: environment == 'prod' ? 3 : 1
    partitionCount: 1
    disableLocalAuth: true
    publicNetworkAccess: privateNetworking ? 'disabled' : 'enabled'
  }
}

resource lakeDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-lake-blob'
  scope: blobService
  properties: {
    workspaceId: law.id
    logs: [{ categoryGroup: 'audit', enabled: true }]
    metrics: [{ category: 'Transaction', enabled: true }]
  }
}

resource searchDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-search'
  scope: search
  properties: {
    workspaceId: law.id
    logs: [{ categoryGroup: 'audit', enabled: true }]
  }
}

resource kvDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'diag-kv'
  scope: kv
  properties: {
    workspaceId: law.id
    logs: [{ categoryGroup: 'audit', enabled: true }]
  }
}

output storageAccountName string = lake.name
output storageAccountId string = lake.id
output keyVaultName string = kv.name
output keyVaultId string = kv.id
output searchServiceName string = search.name
output searchServiceId string = search.id
