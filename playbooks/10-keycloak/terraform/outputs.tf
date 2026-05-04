output "realm_id" {
  description = "Keycloak realm id."
  value       = keycloak_realm.main.id
}

output "realm_issuer_url" {
  description = "OIDC issuer URL for the managed realm."
  value       = "${var.keycloak_url}/realms/${keycloak_realm.main.realm}"
}

output "client_ids" {
  description = "Map of configured client name -> Keycloak internal id."
  value       = { for name, c in keycloak_openid_client.clients : name => c.id }
}
