mock_provider "keycloak" {}

variables {
  keycloak_url            = "https://keycloak.example.invalid"
  keycloak_admin_username = "test"
  keycloak_admin_password = "FAKE_TEST_PASSWORD"
  realm_name              = "test"
}

run "public_pkce_and_existing_confidential_client" {
  command = plan
  variables {
    clients = [
      {
        name                       = "argocd"
        access_type                = "PUBLIC"
        pkce_code_challenge_method = "S256"
        groups_claim               = true
        redirect_uris              = ["https://argocd.example.com/auth/callback", "https://argocd.example.com/pkce/verify", "http://localhost:8085/auth/callback"]
      },
      {
        name          = "existing"
        client_secret = "FAKE_EXISTING_SECRET"
        redirect_uris = ["https://existing.example.com/callback"]
      },
    ]
  }
  assert {
    condition = (
      keycloak_openid_client.clients["argocd"].access_type == "PUBLIC" &&
      keycloak_openid_client.clients["argocd"].pkce_code_challenge_method == "S256" &&
      var.clients[0].client_secret == null &&
      keycloak_openid_client.clients["argocd"].standard_flow_enabled &&
      !keycloak_openid_client.clients["argocd"].implicit_flow_enabled &&
      !keycloak_openid_client.clients["argocd"].direct_access_grants_enabled
    )
    error_message = "Argo CD must use a public authorization-code client with S256 and no configured secret."
  }
  assert {
    condition = (
      keycloak_openid_client.clients["existing"].access_type == "CONFIDENTIAL" &&
      keycloak_openid_client.clients["existing"].client_secret == "FAKE_EXISTING_SECRET" &&
      keycloak_openid_client.clients["existing"].pkce_code_challenge_method == ""
    )
    error_message = "Existing confidential client configuration must be preserved."
  }
  assert {
    condition = (
      keys(keycloak_openid_group_membership_protocol_mapper.client_groups) == ["argocd"] &&
      keycloak_openid_group_membership_protocol_mapper.client_groups["argocd"].add_to_id_token &&
      !keycloak_openid_group_membership_protocol_mapper.client_groups["argocd"].full_path
    )
    error_message = "Only the opted-in client should receive the short-name groups claim."
  }
}

run "reject_public_without_pkce" {
  command = plan
  variables {
    clients = [{ name = "argocd", access_type = "PUBLIC", redirect_uris = ["https://argocd.example.com/auth/callback"] }]
  }
  expect_failures = [var.clients]
}

run "reject_public_with_secret" {
  command = plan
  variables {
    clients = [{ name = "argocd", access_type = "PUBLIC", pkce_code_challenge_method = "S256", client_secret = "FAKE", redirect_uris = [] }]
  }
  expect_failures = [var.clients]
}

run "reject_confidential_without_secret" {
  command = plan
  variables {
    clients = [{ name = "existing", redirect_uris = [] }]
  }
  expect_failures = [var.clients]
}

run "reject_plain_pkce" {
  command = plan
  variables {
    clients = [{ name = "existing", client_secret = "FAKE", pkce_code_challenge_method = "plain", redirect_uris = [] }]
  }
  expect_failures = [var.clients]
}

run "client_audiences_and_optional_scopes" {
  command = plan
  variables {
    clients = [
      {
        name                       = "mcp-clients"
        access_type                = "PUBLIC"
        pkce_code_challenge_method = "S256"
        groups_claim               = true
        redirect_uris              = ["http://localhost/callback", "http://127.0.0.1/callback"]
        audiences                  = ["k8s-mcp"]
        optional_scopes            = ["offline_access"]
      },
      {
        name          = "k8s-mcp"
        client_secret = "FAKE_EXCHANGER_SECRET"
        redirect_uris = ["https://mcp.example.com/oauth2/callback"]
      },
    ]
  }
  assert {
    condition = (
      keys(keycloak_openid_audience_protocol_mapper.client_audiences) == ["mcp-clients:k8s-mcp"] &&
      keycloak_openid_audience_protocol_mapper.client_audiences["mcp-clients:k8s-mcp"].included_client_audience == "k8s-mcp" &&
      keycloak_openid_audience_protocol_mapper.client_audiences["mcp-clients:k8s-mcp"].add_to_access_token &&
      !keycloak_openid_audience_protocol_mapper.client_audiences["mcp-clients:k8s-mcp"].add_to_id_token
    )
    error_message = "Each listed audience must get one access-token-only audience mapper on its client."
  }
  assert {
    condition = (
      keys(keycloak_openid_client_optional_scopes.clients) == ["mcp-clients"] &&
      toset(keycloak_openid_client_optional_scopes.clients["mcp-clients"].optional_scopes) == toset(["offline_access"])
    )
    error_message = "Only a client with optional_scopes may get an explicit optional-scope set."
  }
}

run "reject_self_audience" {
  command = plan
  variables {
    clients = [{ name = "app", client_secret = "FAKE", redirect_uris = ["https://app.example.com/cb"], audiences = ["app"] }]
  }
  expect_failures = [var.clients]
}

run "openwebui_and_exchanger_use_generic_audiences_and_scopes" {
  # Apply only to the mock provider so generated client IDs are known and
  # the assertions can verify each mapper and scope assignment's client.
  command = apply
  variables {
    clients = [
      {
        name                       = "openwebui"
        access_type                = "PUBLIC"
        pkce_code_challenge_method = "S256"
        redirect_uris              = ["https://chat.example.com/oauth/oidc/callback"]
        audiences                  = ["k8s-mcp"]
        optional_scopes            = ["address", "phone", "offline_access", "microprofile-jwt"]
      },
      {
        name                            = "k8s-mcp"
        client_secret                   = "FAKE_EXCHANGER_SECRET"
        pkce_code_challenge_method      = "S256"
        redirect_uris                   = ["https://mcp.example.com/oauth2/callback"]
        standard_token_exchange_enabled = true
        optional_scopes                 = ["address", "phone", "offline_access", "microprofile-jwt", "k8s-cluster"]
      },
    ]
  }
  assert {
    condition = (
      keycloak_openid_client.clients["openwebui"].access_type == "PUBLIC" &&
      keycloak_openid_client.clients["openwebui"].pkce_code_challenge_method == "S256" &&
      var.clients[0].client_secret == null &&
      keycloak_openid_client.clients["k8s-mcp"].access_type == "CONFIDENTIAL" &&
      keycloak_openid_client.clients["k8s-mcp"].client_secret == "FAKE_EXCHANGER_SECRET" &&
      keycloak_openid_client.clients["k8s-mcp"].pkce_code_challenge_method == "S256" &&
      keycloak_openid_client.clients["k8s-mcp"].standard_token_exchange_enabled
    )
    error_message = "Open WebUI must keep public S256 login while MCP remains a confidential S256 token exchanger."
  }
  assert {
    condition = (
      keys(keycloak_openid_audience_protocol_mapper.client_audiences) == ["openwebui:k8s-mcp"] &&
      keycloak_openid_audience_protocol_mapper.client_audiences["openwebui:k8s-mcp"].client_id == keycloak_openid_client.clients["openwebui"].id &&
      keycloak_openid_audience_protocol_mapper.client_audiences["openwebui:k8s-mcp"].included_client_audience == "k8s-mcp" &&
      keycloak_openid_audience_protocol_mapper.client_audiences["openwebui:k8s-mcp"].add_to_access_token &&
      !keycloak_openid_audience_protocol_mapper.client_audiences["openwebui:k8s-mcp"].add_to_id_token
    )
    error_message = "Open WebUI access tokens must carry exactly the MCP audience through the generic mapper."
  }
  assert {
    condition = (
      toset(keys(keycloak_openid_client_optional_scopes.clients)) == toset(["openwebui", "k8s-mcp"]) &&
      keycloak_openid_client_optional_scopes.clients["openwebui"].client_id == keycloak_openid_client.clients["openwebui"].id &&
      keycloak_openid_client_optional_scopes.clients["k8s-mcp"].client_id == keycloak_openid_client.clients["k8s-mcp"].id &&
      toset(keycloak_openid_client_optional_scopes.clients["openwebui"].optional_scopes) == toset(["address", "phone", "offline_access", "microprofile-jwt"]) &&
      toset(keycloak_openid_client_optional_scopes.clients["k8s-mcp"].optional_scopes) == toset(["address", "phone", "offline_access", "microprofile-jwt", "k8s-cluster"]) &&
      keycloak_openid_client_scope.k8s_cluster.name == "k8s-cluster"
    )
    error_message = "Both clients must retain their exact optional scopes, with k8s-cluster assigned only to the exchanger."
  }
}
