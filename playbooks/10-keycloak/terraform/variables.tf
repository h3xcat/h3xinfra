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
    client_secret = optional(string)
    # Preserve existing confidential clients; public browser/CLI clients
    # must explicitly opt in and require PKCE S256 instead of a secret.
    access_type                = optional(string, "CONFIDENTIAL")
    pkce_code_challenge_method = optional(string, "")
    root_url                   = optional(string, "")
    redirect_uris              = list(string)
    # CORS origins. Empty (the default) allows none. "+" allows the
    # origins of redirect_uris. "*" is rejected.
    web_origins = optional(list(string), [])
    # Client roles to create on this client for app-level RBAC (e.g. Open
    # WebUI's `admin`/`user`). When non-empty, a protocol mapper is also added
    # that flattens these client roles into a top-level `roles` claim (id token
    # + userinfo). The roles are created but assignment to users stays manual
    # (Keycloak admin console). The oauth2-proxy-fronted clients don't need
    # this, so it defaults to empty.
    client_roles = optional(list(string), [])
    # Native OIDC consumers such as Argo CD authorize using Keycloak groups.
    groups_claim = optional(bool, false)
    # Enable Keycloak v2 "standard token exchange" (RFC 8693) for this client,
    # so it may exchange a token issued to itself for one carrying a different
    # audience. Used by the Kubernetes MCP server (STS client = openwebui) to
    # mint an `aud=kubernetes` token for the kube-apiserver. Defaults off.
    standard_token_exchange_enabled = optional(bool, false)
    # Client IDs to add to this client's ACCESS token audience (one audience
    # mapper each). A resource server that checks `aud` (for example an MCP
    # server, or an Envoy JWT gate) can then accept this client's tokens.
    audiences = optional(list(string), [])
    # When set, the complete list of OPTIONAL client scopes of this client
    # (replaces the realm defaults). Use it to keep a public client from
    # requesting scopes it must not get, for example a cluster-credential
    # scope. null (the default) keeps the realm's optional scopes.
    optional_scopes = optional(list(string))
  }))
  default = []

  validation {
    condition     = alltrue([for client in var.clients : contains(["CONFIDENTIAL", "PUBLIC"], client.access_type)])
    error_message = "OIDC clients must use CONFIDENTIAL or PUBLIC access_type."
  }

  validation {
    condition = alltrue([for client in var.clients : client.access_type == "PUBLIC" ? (
      client.pkce_code_challenge_method == "S256" && client.client_secret == null
    ) : try(length(trimspace(client.client_secret)) > 0, false)])
    error_message = "PUBLIC clients require PKCE S256 and no client_secret; CONFIDENTIAL clients require a non-empty client_secret."
  }

  validation {
    condition     = alltrue([for client in var.clients : contains(["", "S256"], client.pkce_code_challenge_method)])
    error_message = "PKCE must be unset or S256; plain challenges are not supported."
  }

  validation {
    condition     = alltrue([for client in var.clients : !contains(client.web_origins, "*")])
    error_message = "web_origins must not contain \"*\"; list explicit origins or use \"+\" for the redirect URI origins."
  }

  validation {
    condition     = alltrue([for client in var.clients : alltrue([for aud in client.audiences : aud != client.name])])
    error_message = "audiences must not contain the client's own name; Keycloak already sets azp to it."
  }

  validation {
    condition     = alltrue([for client in var.clients : client.optional_scopes == null || !contains(["openwebui", "k8s-mcp"], client.name)])
    error_message = "kubernetes_oidc.tf manages the optional scopes of openwebui and k8s-mcp; do not set optional_scopes on them."
  }
}
