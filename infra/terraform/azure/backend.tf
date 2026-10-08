# Remote state in Azure Storage with Entra ID auth (no access keys). Values come from
# envs/<env>.backend.hcl plus -backend-config flags in .github/scripts/deploy.sh.
terraform {
  backend "azurerm" {
    use_azuread_auth = true
  }
}
