# Remote state in S3 with native locking; bucket and region come from envs/<env>.backend.hcl or -backend-config.
terraform {
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
