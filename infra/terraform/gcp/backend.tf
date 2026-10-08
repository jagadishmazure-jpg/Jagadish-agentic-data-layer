# Remote state in a Cloud Storage bucket; the bucket name comes from envs/<env>.backend.hcl or -backend-config.
terraform {
  backend "gcs" {}
}
