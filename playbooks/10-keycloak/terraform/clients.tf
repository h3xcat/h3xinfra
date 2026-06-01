locals {
  # Turn the client list into a map keyed by name so we can use for_each.
  clients_by_name = { for c in var.clients : c.name => c }

  # Clients that define their own (client-scoped) roles for app-level RBAC.
  clients_with_roles = { for k, c in local.clients_by_name : k => c if length(c.client_roles) > 0 }

  # Flattened (client, role) pairs so each client role is its own resource.
  client_role_pairs = {
    for pair in flatten([
      for k, c in local.clients_with_roles : [
        for r in c.client_roles : { key = "${k}:${r}", client = k, role = r }
      ]
    ]) : pair.key => pair
  }
}

resource "keycloak_openid_client" "clients" {
  for_each = local.clients_by_name

  realm_id  = keycloak_realm.main.id
  client_id = each.value.name
  name      = each.value.name
  enabled   = true

  access_type   = "CONFIDENTIAL"
  client_secret = each.value.client_secret

  standard_flow_enabled        = true
  direct_access_grants_enabled = false
  implicit_flow_enabled        = false
  service_accounts_enabled     = false

  root_url            = each.value.root_url
  valid_redirect_uris = each.value.redirect_uris
  web_origins         = length(each.value.web_origins) > 0 ? each.value.web_origins : ["+"]

  # Keycloak v2 standard token exchange (RFC 8693). Off unless a client opts in.
  standard_token_exchange_enabled = each.value.standard_token_exchange_enabled

  login_theme = "keycloak"
}

# Expose the email claim by default — most downstream consumers (oauth2-proxy
# etc.) require it to identify users.
resource "keycloak_openid_user_attribute_protocol_mapper" "email_mapper" {
  for_each = keycloak_openid_client.clients

  realm_id  = keycloak_realm.main.id
  client_id = each.value.id
  name      = "email"

  user_attribute   = "email"
  claim_name       = "email"
  claim_value_type = "String"

  add_to_id_token     = true
  add_to_access_token = true
  add_to_userinfo     = true
}

# Client-scoped roles for app-level RBAC. Created here so they exist for
# assignment; user->role assignment is done manually in the Keycloak admin
# console (not managed by Terraform).
resource "keycloak_role" "client_roles" {
  for_each = local.client_role_pairs

  realm_id  = keycloak_realm.main.id
  client_id = keycloak_openid_client.clients[each.value.client].id
  name      = each.value.role
}

# Flatten this client's own roles into a top-level `roles` claim. Open WebUI
# (OAUTH_ROLES_CLAIM=roles) reads this to map users to admin/user, so the
# roles stay namespaced to the client instead of polluting the realm. Depends
# on the roles existing first.
resource "keycloak_openid_user_client_role_protocol_mapper" "client_roles" {
  for_each = local.clients_with_roles

  realm_id                    = keycloak_realm.main.id
  client_id                   = keycloak_openid_client.clients[each.key].id
  client_id_for_role_mappings = each.value.name
  name                        = "client-roles"

  claim_name          = "roles"
  claim_value_type    = "String"
  multivalued         = true
  add_to_id_token     = true
  add_to_access_token = true
  add_to_userinfo     = true

  depends_on = [keycloak_role.client_roles]
}
