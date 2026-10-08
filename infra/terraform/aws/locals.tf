locals {
  suffix = "${var.domain}-${var.environment}"
  layers = toset(["bronze", "silver", "gold"])
  tags = {
    workload    = "agentic-data-layer"
    domain      = var.domain
    environment = var.environment
    managed-by  = "terraform"
  }
  oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}
