plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

# Demo stack created and destroyed by deploy.yml / teardown.yml; prevent_destroy would block teardown.
rule "azurerm_resources_missing_prevent_destroy" {
  enabled = false
}
