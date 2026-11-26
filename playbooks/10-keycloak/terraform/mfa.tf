# Apply the MFA browser flow to the application realm.
module "mfa_h3xinfra" {
  source   = "./modules/mfa-flow"
  realm_id = keycloak_realm.main.id

  # Password reset needs the reset e-mail and an OTP code
  # (reset_credentials.tf). The module owns the realm bindings resource.
  reset_credentials_flow = keycloak_authentication_flow.reset_otp.alias

  # Bind the flow only after all of its steps exist. A partial flow can reset
  # a password without the OTP check. Keep each execution and configuration
  # of reset_credentials.tf in this list (tests/test_reset_credentials.py).
  depends_on = [
    keycloak_authentication_execution.reset_otp_choose_user,
    keycloak_authentication_execution.reset_otp_email,
    keycloak_authentication_execution.reset_otp_check_condition,
    keycloak_authentication_execution.reset_otp_check_form,
    keycloak_authentication_execution.reset_otp_deny_condition,
    keycloak_authentication_execution_config.reset_otp_deny_condition,
    keycloak_authentication_execution.reset_otp_deny,
    keycloak_authentication_execution_config.reset_otp_deny,
    keycloak_authentication_execution.reset_otp_password,
  ]
}

# Apply the same MFA browser flow to the master realm so administrative
# logins are also protected. The master realm is built-in and not managed
# as a keycloak_realm resource — referencing it by id is sufficient.
# Terraform does not change the reset-credentials flow of the master realm.
module "mfa_master" {
  source   = "./modules/mfa-flow"
  realm_id = "master"
}
