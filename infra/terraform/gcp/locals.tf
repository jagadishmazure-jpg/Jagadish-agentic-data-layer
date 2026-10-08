locals {
  suffix = "${var.domain}-${var.environment}"
  layers = toset(["bronze", "silver", "gold"])
  # owner and name kept separate so the subject claim is always repo:<owner>/<name>:environment:<env>
  github_owner = split("/", var.github_repository)[0]
  github_name  = split("/", var.github_repository)[1]
  labels = {
    workload    = "agentic-data-layer"
    domain      = var.domain
    environment = var.environment
    managed-by  = "terraform"
  }
}
