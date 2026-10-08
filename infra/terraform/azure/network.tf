# Optional private networking: a VNet with one subnet for private endpoints, private DNS zones
# linked to it, and private endpoints for the lake (dfs and blob), AI Search and Key Vault.
locals {
  private_zones = var.private_networking ? {
    dfs    = "privatelink.dfs.core.windows.net"
    blob   = "privatelink.blob.core.windows.net"
    search = "privatelink.search.windows.net"
    vault  = "privatelink.vaultcore.azure.net"
  } : {}
  endpoints = var.private_networking ? {
    dfs    = { id = azurerm_storage_account.lake.id, subresource = "dfs" }
    blob   = { id = azurerm_storage_account.lake.id, subresource = "blob" }
    search = { id = azurerm_search_service.this.id, subresource = "searchService" }
    vault  = { id = azurerm_key_vault.this.id, subresource = "vault" }
  } : {}
}

resource "azurerm_virtual_network" "this" {
  count               = var.private_networking ? 1 : 0
  name                = "vnet-adl-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = [var.vnet_address_space]
  tags                = local.tags
}

resource "azurerm_network_security_group" "endpoints" {
  count               = var.private_networking ? 1 : 0
  name                = "nsg-adl-pe-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_subnet" "endpoints" {
  count                             = var.private_networking ? 1 : 0
  name                              = "snet-private-endpoints"
  resource_group_name               = azurerm_resource_group.this.name
  virtual_network_name              = azurerm_virtual_network.this[0].name
  address_prefixes                  = [cidrsubnet(var.vnet_address_space, 2, 0)]
  private_endpoint_network_policies = "Enabled"
}

resource "azurerm_subnet_network_security_group_association" "endpoints" {
  count                     = var.private_networking ? 1 : 0
  subnet_id                 = azurerm_subnet.endpoints[0].id
  network_security_group_id = azurerm_network_security_group.endpoints[0].id
}

resource "azurerm_private_dns_zone" "zone" {
  for_each            = local.private_zones
  name                = each.value
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "link" {
  for_each              = local.private_zones
  name                  = "link-${each.key}"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.zone[each.key].name
  virtual_network_id    = azurerm_virtual_network.this[0].id
  tags                  = local.tags
}

resource "azurerm_private_endpoint" "pe" {
  for_each            = local.endpoints
  name                = "pe-adl-${each.key}-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  subnet_id           = azurerm_subnet.endpoints[0].id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-${each.key}"
    private_connection_resource_id = each.value.id
    subresource_names              = [each.value.subresource]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.zone[each.key].id]
  }
}
