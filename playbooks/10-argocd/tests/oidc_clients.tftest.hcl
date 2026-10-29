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
