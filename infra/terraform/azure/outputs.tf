output "AZURE_RESOURCE_GROUP" {
  description = "Resource group holding the stack."
  value       = azurerm_resource_group.this.name
}

output "LAKE_ACCOUNT" {
  description = "ADLS Gen2 account (the Databricks adapter's account; OneLake uses a Fabric workspace instead)."
  value       = azurerm_storage_account.lake.name
}

output "SEARCH_ENDPOINT" {
  description = "Azure AI Search endpoint for adl.knowledge.azure_search."
  value       = "https://${azurerm_search_service.this.name}.search.windows.net"
}

output "AGENT_IDENTITY_CLIENT_ID" {
  description = "Client id of the agents' managed identity (read gold and the search index only)."
  value       = azurerm_user_assigned_identity.agents.client_id
}

output "PIPELINE_IDENTITY_CLIENT_ID" {
  description = "Client id of the pipeline's managed identity."
  value       = azurerm_user_assigned_identity.pipeline.client_id
}

output "KEY_VAULT_URI" {
  description = "Key Vault holding the pseudonymisation key."
  value       = azurerm_key_vault.this.vault_uri
}
