# Offline plan tests: mocked provider, no Google Cloud credentials, nothing created.
mock_provider "google" {}

override_resource {
  target          = google_iam_workload_identity_pool.github
  override_during = plan
  values = {
    name                      = "projects/123456789/locations/global/workloadIdentityPools/adl-github-dev"
    workload_identity_pool_id = "adl-github-dev"
  }
}

override_resource {
  target          = google_service_account.pipeline
  override_during = plan
  values = {
    name  = "projects/wf-adl-dev/serviceAccounts/adl-pipeline-dev@wf-adl-dev.iam.gserviceaccount.com"
    email = "adl-pipeline-dev@wf-adl-dev.iam.gserviceaccount.com"
  }
}

override_resource {
  target          = google_service_account.agents
  override_during = plan
  values = {
    email = "adl-agents-dev@wf-adl-dev.iam.gserviceaccount.com"
  }
}

run "dev_defaults" {
  command = plan

  variables {
    environment = "dev"
    project_id  = "wf-adl-dev"
  }

  assert {
    condition     = google_storage_bucket.lake.public_access_prevention == "enforced" && google_storage_bucket.lake.uniform_bucket_level_access
    error_message = "the lake bucket can never be public and has no object ACLs"
  }

  assert {
    condition     = length(google_bigquery_dataset.layer) == 3
    error_message = "one dataset per layer"
  }

  assert {
    condition     = google_bigquery_dataset_iam_member.agents_gold.role == "roles/bigquery.dataViewer" && google_bigquery_dataset_iam_member.agents_gold.dataset_id == "retail_gold"
    error_message = "agents read the gold dataset only"
  }

  assert {
    condition     = strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "jagadishmazure-jpg/Jagadish-agentic-data-layer")
    error_message = "the pool trusts only this repository"
  }
}

run "rejects_bad_repository" {
  command = plan

  variables {
    environment       = "dev"
    project_id        = "wf-adl-dev"
    github_repository = "not a repo"
  }

  expect_failures = [var.github_repository]
}
