# Credentials come from the default chain: in GitHub Actions, aws-actions/configure-aws-credentials
# assumes the deploy role with AssumeRoleWithWebIdentity (OIDC); never long-lived access keys.
provider "aws" {
  region = var.region
  default_tags {
    tags = local.tags
  }
}
