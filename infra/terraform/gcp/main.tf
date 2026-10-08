# ---------------------------------------------------------------- lake (Cloud Storage)
resource "google_storage_bucket" "lake" {
  name                        = "${var.project_id}-adl-${local.suffix}-lake"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.environment == "dev"
  labels                      = local.labels

  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }

  logging {
    log_bucket = google_storage_bucket.access_logs.name
  }
}

resource "google_storage_bucket" "access_logs" {
  name                        = "${var.project_id}-adl-${local.suffix}-logs"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.environment == "dev"
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }
}

# ---------------------------------------------------------------- warehouse (BigQuery, one dataset per layer)
resource "google_bigquery_dataset" "layer" {
  for_each                   = local.layers
  dataset_id                 = "${var.domain}_${each.key}"
  location                   = var.region
  description                = "${each.key} layer of the ${var.domain} data products"
  delete_contents_on_destroy = var.environment == "dev"
  labels                     = local.labels
}

# ---------------------------------------------------------------- identities
resource "google_service_account" "pipeline" {
  account_id   = "adl-pipeline-${var.environment}"
  display_name = "Agentic data layer pipeline (${var.environment})"
}

resource "google_service_account" "agents" {
  account_id   = "adl-agents-${var.environment}"
  display_name = "Agentic data layer agents, read gold only (${var.environment})"
}

resource "google_storage_bucket_iam_member" "pipeline_lake" {
  bucket = google_storage_bucket.lake.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_bigquery_dataset_iam_member" "pipeline_layers" {
  for_each   = local.layers
  dataset_id = google_bigquery_dataset.layer[each.key].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

# Agents read the gold dataset only; bronze, silver and PII tables are out of reach by IAM.
resource "google_bigquery_dataset_iam_member" "agents_gold" {
  dataset_id = google_bigquery_dataset.layer["gold"].dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.agents.email}"
}

resource "google_project_iam_member" "agents_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.agents.email}"
}

resource "google_project_iam_member" "pipeline_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

# ---------------------------------------------------------------- workload identity federation for GitHub Actions
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "adl-github-${var.environment}"
  display_name              = "GitHub Actions (${var.environment})"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.sub == 'repo:${local.github_owner}/${local.github_name}:environment:${var.environment}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_impersonates_pipeline" {
  service_account_id = google_service_account.pipeline.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
