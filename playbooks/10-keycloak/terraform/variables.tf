variable "keycloak_url" {
  description = "Base URL of the Keycloak instance (no trailing slash)."
  type        = string
}

variable "keycloak_admin_username" {
  description = "Master realm admin username used by the provider."
  type        = string
}

variable "keycloak_admin_password" {
  description = "Master realm admin password used by the provider."
  type        = string
  sensitive   = true
}

variable "realm_name" {
  description = "Name (id) of the realm to manage."
  type        = string
}

variable "realm_display_name" {
  description = "Human-friendly realm display name."
  type        = string
  default     = ""
}

variable "smtp" {
  description = "Realm SMTP settings."
  type = object({
    enabled           = bool
    host              = string
    port              = number
    ssl               = bool
    from              = string
    from_display_name = string
    auth_enabled      = bool
    username          = string
    password          = string
  })
  default = {
    enabled           = false
    host              = ""
    port              = 465
    ssl               = true
    from              = ""
    from_display_name = ""
    auth_enabled      = false
    username          = ""
    password          = ""
  }
  sensitive = true
}

variable "clients" {
  description = "List of OIDC clients to provision in the realm."
  type = list(object({
    name          = string
    client_secret = string
    root_url      = optional(string, "")
    redirect_uris = list(string)
    web_origins   = optional(list(string), [])
  }))
  default = []
}
