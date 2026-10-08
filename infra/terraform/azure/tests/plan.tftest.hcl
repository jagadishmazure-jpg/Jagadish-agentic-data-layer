# Offline plan tests: mocked provider, no Azure credentials, nothing created.
#   terraform init -backend=false && terraform test
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      subscription_id = "00000000-0000-0000-0000-000000000002"
      object_id       = "00000000-0000-0000-0000-000000000003"
    }
  }
}

override_resource {
  target          = azurerm_storage_account.lake
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000002/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/stadl"
  }
}

override_resource {
  target          = azurerm_storage_container.layer
  override_during = plan
  values = {
    id = "/subscriptions/00000000-0000-0000-0000-000000000002/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/stadl/blobServices/default/containers/gold"
  }
}

run "dev_defaults" {
  command = plan

  variables {
    environment = "dev"
  }

  assert {
    condition     = azurerm_resource_group.this.name == "rg-adl-retail-dev-eus2-001"
    error_message = "resource group follows the CAF naming pattern"
  }

  assert {
    condition     = azurerm_storage_account.lake.is_hns_enabled && !azurerm_storage_account.lake.shared_access_key_enabled
    error_message = "the lake is ADLS Gen2 and accepts Entra ID auth only"
  }

  assert {
    condition     = length(azurerm_storage_container.layer) == 3
    error_message = "one container per layer"
  }

  assert {
    condition     = !azurerm_search_service.this.local_authentication_enabled && !azurerm_log_analytics_workspace.this.local_authentication_enabled
    error_message = "AI Search and Log Analytics accept Entra ID tokens only (no API keys)"
  }

  assert {
    condition     = azurerm_role_assignment.agents_gold.role_definition_name == "Storage Blob Data Reader"
    error_message = "agents can only read"
  }

  assert {
    condition     = length(azurerm_fabric_capacity.this) == 0 && length(azurerm_private_endpoint.pe) == 0
    error_message = "Fabric capacity and private endpoints are opt-in"
  }
}

run "prod_private" {
  command = plan

  variables {
    environment        = "prod"
    private_networking = true
  }

  assert {
    condition     = length(azurerm_private_endpoint.pe) == 4 && !azurerm_storage_account.lake.public_network_access_enabled
    error_message = "prod uses private endpoints for dfs, blob, search and vault and no public access"
  }

  assert {
    condition     = azurerm_storage_account.lake.account_replication_type == "ZRS" && azurerm_search_service.this.replica_count == 3
    error_message = "prod is zone redundant and search has three replicas for its SLA"
  }
}

run "fabric_capacity" {
  command = plan

  variables {
    environment            = "dev"
    enable_fabric_capacity = true
    fabric_admin_upn       = "data-admin@wrenfield.example"
  }

  assert {
    condition     = azurerm_fabric_capacity.this[0].sku[0].name == "F2"
    error_message = "the smallest Fabric capacity"
  }
}

run "rejects_unknown_environment" {
  command = plan

  variables {
    environment = "staging"
  }

  expect_failures = [var.environment]
}
