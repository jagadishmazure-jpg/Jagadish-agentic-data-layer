// Optional Microsoft Fabric capacity (F2) for OneLake and the SQL analytics endpoint. Billed while running.
param location string
param name string
param adminUpn string
param tags object

resource capacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'F2', tier: 'Fabric' }
  properties: {
    administration: { members: [adminUpn] }
  }
}
