// Private networking: a VNet, an NSG-protected subnet for private endpoints, private DNS zones and
// private endpoints for the lake (dfs and blob), AI Search and Key Vault.
param location string
param suffix string
param storageAccountId string
param keyVaultId string
param searchServiceId string
param tags object
param addressSpace string = '10.42.0.0/24'

var endpoints = [
  { key: 'dfs', id: storageAccountId, group: 'dfs', zone: 'privatelink.dfs.${az.environment().suffixes.storage}' }
  { key: 'blob', id: storageAccountId, group: 'blob', zone: 'privatelink.blob.${az.environment().suffixes.storage}' }
  { key: 'search', id: searchServiceId, group: 'searchService', zone: 'privatelink.search.windows.net' }
  { key: 'vault', id: keyVaultId, group: 'vault', zone: 'privatelink.vaultcore.azure.net' }
]

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'nsg-adl-pe-${suffix}'
  location: location
  tags: tags
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-adl-${suffix}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: [addressSpace] }
    subnets: [
      {
        name: 'snet-private-endpoints'
        properties: {
          addressPrefix: cidrSubnet(addressSpace, 26, 0)
          networkSecurityGroup: { id: nsg.id }
          privateEndpointNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [
  for e in endpoints: {
    name: e.zone
    location: 'global'
    tags: tags
  }
]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [
  for (e, i) in endpoints: {
    parent: zones[i]
    name: 'link-${e.key}'
    location: 'global'
    tags: tags
    properties: {
      registrationEnabled: false
      virtualNetwork: { id: vnet.id }
    }
  }
]

resource pes 'Microsoft.Network/privateEndpoints@2024-05-01' = [
  for e in endpoints: {
    name: 'pe-adl-${e.key}-${suffix}'
    location: location
    tags: tags
    properties: {
      subnet: { id: vnet.properties.subnets[0].id }
      privateLinkServiceConnections: [
        {
          name: 'psc-${e.key}'
          properties: {
            privateLinkServiceId: e.id
            groupIds: [e.group]
          }
        }
      ]
    }
  }
]

resource zoneGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [
  for (e, i) in endpoints: {
    parent: pes[i]
    name: 'default'
    properties: {
      privateDnsZoneConfigs: [{ name: e.key, properties: { privateDnsZoneId: zones[i].id } }]
    }
  }
]
