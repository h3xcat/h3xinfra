# Keep the existing mapper and complete optional-scope assignments while their
# configuration moves to keycloak.clients. Keep these moves until the owner's
# NAS-backed state records this pending migration; see the layer README.
moved {
  from = keycloak_openid_audience_protocol_mapper.openwebui_k8s_mcp_audience
  to   = keycloak_openid_audience_protocol_mapper.client_audiences["openwebui:k8s-mcp"]
}

moved {
  from = keycloak_openid_client_optional_scopes.openwebui
  to   = keycloak_openid_client_optional_scopes.clients["openwebui"]
}

moved {
  from = keycloak_openid_client_optional_scopes.k8s_mcp
  to   = keycloak_openid_client_optional_scopes.clients["k8s-mcp"]
}
