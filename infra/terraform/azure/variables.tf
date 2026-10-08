variable "environment" {
  description = "Deployment environment."
  type        = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be dev or prod."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "eastus2"
}

variable "domain" {
  description = "Business domain the stack serves (one stack per domain keeps blast radius small)."
  type        = string
  default     = "retail"
  validation {
    condition     = can(regex("^[a-z]{3,12}$", var.domain))
    error_message = "domain must be 3-12 lowercase letters."
  }
}

variable "private_networking" {
  description = "Private endpoints for storage, AI Search and Key Vault, and no public network access."
  type        = bool
  default     = false
}

variable "enable_fabric_capacity" {
  description = "Create a Microsoft Fabric capacity (F2) for OneLake and the SQL analytics endpoint. Billed while running."
  type        = bool
  default     = false
}

variable "fabric_admin_upn" {
  description = "Fabric capacity administrator (user principal name). Required when enable_fabric_capacity is true."
  type        = string
  default     = ""
  validation {
    condition     = var.fabric_admin_upn == "" || can(regex("^[^@\\s]+@[^@\\s]+$", var.fabric_admin_upn))
    error_message = "fabric_admin_upn must be a user principal name."
  }
}

variable "vnet_address_space" {
  description = "Address space for the private networking VNet."
  type        = string
  default     = "10.42.0.0/24"
}

variable "tags" {
  description = "Extra tags merged into every resource."
  type        = map(string)
  default     = {}
}
