# Credentials come from Application Default Credentials: in GitHub Actions, workload identity
# federation (google-github-actions/auth with a workload identity provider); never a key file.
provider "google" {
  project = var.project_id
  region  = var.region
}
