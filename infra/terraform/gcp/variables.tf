variable "environment" {
  description = "Deployment environment."
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "project_id" {
  description = "Google Cloud project id."
  type        = string
}

variable "region" {
  description = "Region for the bucket and BigQuery datasets."
  type        = string
  default     = "us-east4"
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
  description = "owner/name of the only GitHub repository trusted by the workload identity pool."
  type        = string
  default     = "jagadishmazure-jpg/Jagadish-agentic-data-layer"
  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must be owner/name."
  }
}
