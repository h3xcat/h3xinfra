# =============================================================================
# Kubernetes cluster auth via OIDC token exchange.
#
# Lets Open WebUI forward a user's token to the Kubernetes MCP server, which
# (authenticating as its own `k8s-mcp` confidential client) exchanges it — RFC
# 8693 standard token exchange — for a short-lived token carrying `aud=kubernetes`
# plus the user's `groups`, which the kube-apiserver validates and maps to RBAC.
# Using k8s-mcp (not openwebui's login client) as the exchanger keeps the
# pod-resident STS secret exchange-only and decoupled from Open WebUI login.
#
# The `aud=kubernetes` audience and the groups claim live in an OPTIONAL client
# scope (`k8s-cluster`) requested ONLY during the exchange (via the MCP server's
# sts_scopes). Normal Open WebUI logins never carry that audience, so an ordinary
# login token is never a cluster credential.
# =============================================================================

# Audience-target client representing the kube-apiserver. The MCP server's RFC
# 8693 exchanger always sends `audience=<client>`, and Keycloak then sets the
# exchanged token's `aud` to this client — which the kube-apiserver trusts. The
# client initiates no flows and its secret is unused at runtime (Keycloak
# autogenerates one); it exists purely as the exchange audience.
resource "keycloak_openid_client" "kubernetes" {
  realm_id  = keycloak_realm.main.id
  client_id = "kubernetes"
  name      = "kubernetes"
  enabled   = true

  access_type                  = "CONFIDENTIAL"
  standard_flow_enabled        = false
  direct_access_grants_enabled = false
  implicit_flow_enabled        = false
  service_accounts_enabled     = false
}

# Optional client scope requested during token exchange (carries the user's
# groups into the exchanged token and makes the `kubernetes` audience available
# to the k8s-mcp exchanger). Assigned to k8s-mcp below, pulled in via the MCP
# server's sts_scopes. Optional (not default) so normal logins never get the
# kubernetes audience — only the exchange does.
resource "keycloak_openid_client_scope" "k8s_cluster" {
  realm_id               = keycloak_realm.main.id
  name                   = "k8s-cluster"
  description            = "Cluster-auth audience + groups, requested during token exchange (managed by Terraform)."
  include_in_token_scope = true
}

# Make the `kubernetes` client audience AVAILABLE to the exchanger (Keycloak v2
# rejects an exchange to an audience the requesting client can't reach). Lives on
# the optional scope (assigned to k8s-mcp) so it only applies during the exchange.
resource "keycloak_openid_audience_protocol_mapper" "k8s_cluster_audience" {
  realm_id        = keycloak_realm.main.id
  client_scope_id = keycloak_openid_client_scope.k8s_cluster.id
  name            = "k8s-cluster-audience"

  included_client_audience = keycloak_openid_client.kubernetes.client_id
  add_to_id_token          = false
  add_to_access_token      = true
}

# Flatten the user's Keycloak group memberships into a `groups` claim
# (top-level names, not full paths) so the kube-apiserver can map them to
# Kubernetes groups (oidc:<group>).
resource "keycloak_openid_group_membership_protocol_mapper" "k8s_cluster_groups" {
  realm_id        = keycloak_realm.main.id
  client_scope_id = keycloak_openid_client_scope.k8s_cluster.id
  name            = "k8s-cluster-groups"

  claim_name          = "groups"
  full_path           = false
  add_to_id_token     = false
  add_to_access_token = true
  add_to_userinfo     = false
}

# Make the exchange subject token self-audienced (aud contains "openwebui") so
# the MCP server's `oauth_audience = "openwebui"` validation of the INCOMING
# token passes. This is on the openwebui client's normal tokens (harmless).
resource "keycloak_openid_audience_protocol_mapper" "openwebui_self_audience" {
  realm_id  = keycloak_realm.main.id
  client_id = keycloak_openid_client.clients["openwebui"].id
  name      = "self-audience"

  included_custom_audience = "openwebui"
  add_to_id_token          = false
  add_to_access_token      = true
}

# Put the `k8s-mcp` client in openwebui's token audience so k8s-mcp (the STS
# exchanger) is allowed to exchange a user's openwebui-issued token: Keycloak v2
# standard exchange requires the requesting client to appear in the subject
# token's `aud`. On openwebui's normal access tokens (harmless; k8s-mcp is a
# confidential client the user can't drive directly).
resource "keycloak_openid_audience_protocol_mapper" "openwebui_k8s_mcp_audience" {
  realm_id  = keycloak_realm.main.id
  client_id = keycloak_openid_client.clients["openwebui"].id
  name      = "k8s-mcp-audience"

  included_client_audience = keycloak_openid_client.clients["k8s-mcp"].client_id
  add_to_id_token          = false
  add_to_access_token      = true
}

# openwebui's built-in optional scopes (listed explicitly because this resource
# manages the client's full optional-scope set). k8s-cluster is NOT here: the
# exchange is performed by k8s-mcp, so the scope is assigned to that client below.
resource "keycloak_openid_client_optional_scopes" "openwebui" {
  realm_id  = keycloak_realm.main.id
  client_id = keycloak_openid_client.clients["openwebui"].id

  optional_scopes = [
    "address",
    "phone",
    "offline_access",
    "microprofile-jwt",
  ]
}

# Assign k8s-cluster as an OPTIONAL scope to the k8s-mcp exchanger (requested
# only during the exchange via the MCP server's sts_scopes). Built-in optional
# scopes listed explicitly because this resource manages the full set.
resource "keycloak_openid_client_optional_scopes" "k8s_mcp" {
  realm_id  = keycloak_realm.main.id
  client_id = keycloak_openid_client.clients["k8s-mcp"].id

  optional_scopes = [
    "address",
    "phone",
    "offline_access",
    "microprofile-jwt",
    keycloak_openid_client_scope.k8s_cluster.name,
  ]
}

# Realm groups mapped to Kubernetes RBAC by ClusterRoleBindings
# (playbooks/29-kubernetes-mcp): oidc:k8s-admins -> cluster-admin,
# oidc:k8s-viewers -> view. Membership is assigned manually in the Keycloak
# admin console (consistent with how client-role assignment is handled).
resource "keycloak_group" "k8s_admins" {
  realm_id = keycloak_realm.main.id
  name     = "k8s-admins"
}

resource "keycloak_group" "k8s_viewers" {
  realm_id = keycloak_realm.main.id
  name     = "k8s-viewers"
}

output "k8s_oidc_groups" {
  description = "Keycloak groups for cluster RBAC (assign members in the admin console)."
  value       = [keycloak_group.k8s_admins.name, keycloak_group.k8s_viewers.name]
}
