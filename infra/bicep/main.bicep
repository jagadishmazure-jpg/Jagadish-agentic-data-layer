// Azure stack for the agentic data layer (the Bicep twin of infra/terraform/azure).
// Subscription scope: creates the resource group, then the lake, Key Vault, AI Search, Log Analytics,
// identities with least-privilege roles, optional private endpoints and an optional Fabric capacity.
// Never deployed from this repository: deploy.yml is gated by DEPLOY_ENABLED.
targetScope = 'subscription'

@allowed(['dev', 'prod'])
param environment string

param location string = 'eastus2'

@minLength(3)
@maxLength(12)
param domain string = 'retail'

param privateNetworking bool = false

param enableFabricCapacity bool = false

param fabricAdminUpn string = ''

var shortRegion = {
  eastus2: 'eus2'
  westus2: 'wus2'
  westeurope: 'weu'
}
var region = shortRegion[?location] ?? substring(replace(location, '-', ''), 0, 4)
var suffix = '${domain}-${environment}-${region}'
var tags = {
  workload: 'agentic-data-layer'
  domain: domain
  environment: environment
  'managed-by': 'bicep'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-adl-${suffix}-001'
  location: location
  tags: tags
}

module data 'modules/data.bicep' = {
  name: 'adl-data'
  scope: rg
  params: {
    location: location
    suffix: suffix
    compact: take('adl${domain}${environment}${region}', 20)
    environment: environment
    privateNetworking: privateNetworking
    tags: tags
  }
}

module identity 'modules/identity.bicep' = {
  name: 'adl-identity'
  scope: rg
  params: {
    location: location
    suffix: suffix
    storageAccountName: data.outputs.storageAccountName
    keyVaultName: data.outputs.keyVaultName
    searchServiceName: data.outputs.searchServiceName
    tags: tags
  }
}

module network 'modules/network.bicep' = if (privateNetworking) {
  name: 'adl-network'
  scope: rg
  params: {
    location: location
    suffix: suffix
    storageAccountId: data.outputs.storageAccountId
    keyVaultId: data.outputs.keyVaultId
    searchServiceId: data.outputs.searchServiceId
    tags: tags
  }
}

module fabric 'modules/fabric.bicep' = if (enableFabricCapacity) {
  name: 'adl-fabric'
  scope: rg
  params: {
    location: location
    name: 'fc${take('adl${domain}${environment}${region}', 20)}'
    adminUpn: fabricAdminUpn
    tags: tags
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output LAKE_ACCOUNT string = data.outputs.storageAccountName
output SEARCH_ENDPOINT string = 'https://${data.outputs.searchServiceName}.search.windows.net'
output AGENT_IDENTITY_CLIENT_ID string = identity.outputs.agentsClientId
output PIPELINE_IDENTITY_CLIENT_ID string = identity.outputs.pipelineClientId
