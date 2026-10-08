data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "this" {
  name     = "rg-adl-${local.suffix}-001"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- observability
resource "azurerm_log_analytics_workspace" "this" {
  name                         = "log-adl-${local.suffix}"
  location                     = azurerm_resource_group.this.location
  resource_group_name          = azurerm_resource_group.this.name
  sku                          = "PerGB2018"
  retention_in_days            = 30
  daily_quota_gb               = 1
  local_authentication_enabled = false
  tags                         = local.tags
}

# ---------------------------------------------------------------- lake (ADLS Gen2)
resource "azurerm_storage_account" "lake" {
  name                              = "st${local.compact}"
  resource_group_name               = azurerm_resource_group.this.name
  location                          = azurerm_resource_group.this.location
  account_tier                      = "Standard"
  account_replication_type          = var.environment == "prod" ? "ZRS" : "LRS"
  account_kind                      = "StorageV2"
  is_hns_enabled                    = true
  min_tls_version                   = "TLS1_2"
  https_traffic_only_enabled        = true
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  public_network_access_enabled     = !var.private_networking
  infrastructure_encryption_enabled = true
  local_user_enabled                = false
  sftp_enabled                      = false

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  network_rules {
    default_action = var.private_networking ? "Deny" : "Allow"
    bypass         = ["AzureServices"]
  }

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

resource "azurerm_storage_container" "layer" {
  for_each              = local.layers
  name                  = each.key
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "audit" {
  name                  = "audit"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# ---------------------------------------------------------------- secrets (pseudonymisation key)
resource "azurerm_key_vault" "this" {
  name                          = "kv-${local.compact}"
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 30
  public_network_access_enabled = !var.private_networking

  network_acls {
    default_action = var.private_networking ? "Deny" : "Allow"
    bypass         = "AzureServices"
  }

  tags = local.tags
}

# ---------------------------------------------------------------- knowledge (Azure AI Search)
resource "azurerm_search_service" "this" {
  name                          = "srch-adl-${local.suffix}"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  sku                           = "basic"
  replica_count                 = var.environment == "prod" ? 3 : 1
  partition_count               = 1
  local_authentication_enabled  = false
  public_network_access_enabled = !var.private_networking

  identity {
    type = "SystemAssigned"
  }

  tags = local.tags
}

# ---------------------------------------------------------------- optional Fabric capacity (OneLake)
resource "azurerm_fabric_capacity" "this" {
  count                  = var.enable_fabric_capacity ? 1 : 0
  name                   = "fc${local.compact}"
  resource_group_name    = azurerm_resource_group.this.name
  location               = azurerm_resource_group.this.location
  administration_members = [var.fabric_admin_upn]

  sku {
    name = "F2"
    tier = "Fabric"
  }

  tags = local.tags

  lifecycle {
    precondition {
      condition     = var.fabric_admin_upn != ""
      error_message = "fabric_admin_upn is required when enable_fabric_capacity is true."
    }
  }
}

# ---------------------------------------------------------------- identities and least-privilege roles
resource "azurerm_user_assigned_identity" "pipeline" {
  name                = "id-adl-pipeline-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_user_assigned_identity" "agents" {
  name                = "id-adl-agents-${local.suffix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

# The pipeline writes every layer.
resource "azurerm_role_assignment" "pipeline_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.pipeline.principal_id
}

# The pipeline reads the pseudonymisation key (the secret itself is created by the privacy office, not here).
resource "azurerm_role_assignment" "pipeline_key" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.pipeline.principal_id
}

# Agents read only the gold container: bronze, silver and the PII tables are out of reach by role, not just by code.
resource "azurerm_role_assignment" "agents_gold" {
  scope                = azurerm_storage_container.layer["gold"].id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.agents.principal_id
}

resource "azurerm_role_assignment" "agents_search" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_user_assigned_identity.agents.principal_id
}

# ---------------------------------------------------------------- diagnostics
resource "azurerm_monitor_diagnostic_setting" "lake_blob" {
  name                       = "diag-lake-blob"
  target_resource_id         = "${azurerm_storage_account.lake.id}/blobServices/default"
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "audit"
  }

  enabled_metric {
    category = "Transaction"
  }
}

resource "azurerm_monitor_diagnostic_setting" "search" {
  name                       = "diag-search"
  target_resource_id         = azurerm_search_service.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "audit"
  }
}

resource "azurerm_monitor_diagnostic_setting" "key_vault" {
  name                       = "diag-kv"
  target_resource_id         = azurerm_key_vault.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "audit"
  }
}
