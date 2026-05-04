locals {
  # Turn the client list into a map keyed by name so we can use for_each.
  clients_by_name = { for c in var.clients : c.name => c }
}

resource "keycloak_openid_client" "clients" {
  for_each = local.clients_by_name

  realm_id  = keycloak_realm.main.id
  client_id = each.value.name
  name      = each.value.name
  enabled   = true

  access_type   = "CONFIDENTIAL"
  client_secret = each.value.client_secret

  standard_flow_enabled          = true
  direct_access_grants_enabled   = false
  implicit_flow_enabled          = false
  service_accounts_enabled       = false

  root_url      = each.value.root_url
  valid_redirect_uris = each.value.redirect_uris
  web_origins         = length(each.value.web_origins) > 0 ? each.value.web_origins : ["+"]

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
