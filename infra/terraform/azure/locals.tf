locals {
  short = {
    eastus2    = "eus2"
    westus2    = "wus2"
    westeurope = "weu"
  }
  region = lookup(local.short, var.location, substr(replace(var.location, "-", ""), 0, 4))
  suffix = "${var.domain}-${var.environment}-${local.region}"
  # Storage accounts and Key Vaults need globally unique, short, alphanumeric names.
  compact = substr(replace("adl${var.domain}${var.environment}${local.region}", "-", ""), 0, 20)
  layers  = toset(["bronze", "silver", "gold"])
  tags = merge({
    workload    = "agentic-data-layer"
    domain      = var.domain
    environment = var.environment
    managed-by  = "terraform"
    use-case    = "${var.domain}-value-ledger"
  }, var.tags)
}
