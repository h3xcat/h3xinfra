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
# to the k8s-mcp exchanger). Assign it through that client's optional_scopes.
# The MCP server requests it via sts_scopes. Optional (not default), so normal
# logins never get the kubernetes audience — only the exchange does.
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

# Realm groups for downstream Kubernetes RBAC. Group membership alone grants
# no Kubernetes permissions; downstream role bindings define the scope.
# The k8s-admins name does not imply the built-in cluster-admin role.
# Assign membership manually in the Keycloak admin console.
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
