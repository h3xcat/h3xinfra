# Migrate previously root-level browser-mfa resources (h3xinfra realm) into
# the new module path so Terraform updates state in-place instead of
# destroying & recreating the flow.

moved {
  from = keycloak_authentication_flow.browser_mfa
  to   = module.mfa_h3xinfra.keycloak_authentication_flow.browser_mfa
}

moved {
  from = keycloak_authentication_execution.browser_mfa_cookie
  to   = module.mfa_h3xinfra.keycloak_authentication_execution.cookie
}

moved {
  from = keycloak_authentication_execution.browser_mfa_idp_redirector
  to   = module.mfa_h3xinfra.keycloak_authentication_execution.idp_redirector
}

moved {
  from = keycloak_authentication_subflow.browser_mfa_forms
  to   = module.mfa_h3xinfra.keycloak_authentication_subflow.forms
}

moved {
  from = keycloak_authentication_execution.browser_mfa_username_password
  to   = module.mfa_h3xinfra.keycloak_authentication_execution.username_password
}

moved {
  from = keycloak_authentication_subflow.browser_mfa_2fa
  to   = module.mfa_h3xinfra.keycloak_authentication_subflow.twofa
}

moved {
  from = keycloak_authentication_execution.browser_mfa_webauthn
  to   = module.mfa_h3xinfra.keycloak_authentication_execution.webauthn
}

moved {
  from = keycloak_authentication_execution.browser_mfa_otp
  to   = module.mfa_h3xinfra.keycloak_authentication_execution.otp
}

moved {
  from = keycloak_authentication_bindings.browser_mfa
  to   = module.mfa_h3xinfra.keycloak_authentication_bindings.browser_mfa
}

moved {
  from = keycloak_required_action.configure_totp
  to   = module.mfa_h3xinfra.keycloak_required_action.configure_totp
}

moved {
  from = keycloak_required_action.webauthn_register
  to   = module.mfa_h3xinfra.keycloak_required_action.webauthn_register
}
