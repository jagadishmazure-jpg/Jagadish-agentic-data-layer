variable "environment" {
  description = "Deployment environment."
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "domain" {
  description = "Business domain the stack serves."
  type        = string
  default     = "retail"
  validation {
    condition     = can(regex("^[a-z]{3,12}$", var.domain))
    error_message = "domain must be 3-12 lowercase letters."
  }
}

variable "github_repository" {
  description = "owner/name of the only GitHub repository allowed to assume the deploy role."
  type        = string
  default     = "jagadishmazure-jpg/Jagadish-agentic-data-layer"
  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must be owner/name."
  }
}

variable "create_github_oidc_provider" {
  description = "Create the account's GitHub OIDC provider (only one may exist per account)."
  type        = bool
  default     = true
}
