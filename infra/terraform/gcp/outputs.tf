output "LAKE_BUCKET" {
  description = "Cloud Storage bucket for the lake (adl.storage.bigquery.BigQueryStore bucket)."
  value       = google_storage_bucket.lake.name
}

output "DATASETS" {
  description = "BigQuery dataset per layer."
  value       = { for k, d in google_bigquery_dataset.layer : k => d.dataset_id }
}

output "AGENT_SERVICE_ACCOUNT" {
  description = "Service account the agents run as (reads gold only)."
  value       = google_service_account.agents.email
}

output "WORKLOAD_IDENTITY_PROVIDER" {
  description = "Provider name for google-github-actions/auth."
  value       = google_iam_workload_identity_pool_provider.github.name
}
