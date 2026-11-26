# Mocked-provider tests for the reset-credentials flow
# (terraform/reset_credentials.tf). test_groups.py copies the module files and
# this file to a temporary directory and runs `terraform test` there. No
# Keycloak is contacted. test_reset_credentials.py checks the realm binding in
# mfa.tf, because terraform test cannot read resources inside a child module.

mock_provider "keycloak" {}

variables {
  keycloak_url            = "https://keycloak.example.invalid"
  keycloak_admin_username = "test"
  keycloak_admin_password = "FAKE_TEST_PASSWORD"
  realm_name              = "test"
}

run "reset_needs_reset_mail_and_otp" {
  # apply (mocked): the execution ids are only known after apply.
  command = apply

  assert {
    condition = (
      keycloak_authentication_flow.reset_otp.alias == "reset-credentials-otp" &&
      keycloak_authentication_flow.reset_otp.provider_id == "basic-flow" &&
      keycloak_authentication_flow.reset_otp.realm_id == keycloak_realm.main.id
    )
    error_message = "The reset flow must be a top-level basic flow of the application realm."
  }

  # Choose the user, send the reset mail, OTP check, deny, then the password.
  assert {
    condition = [
      for step in [
        keycloak_authentication_execution.reset_otp_choose_user,
        keycloak_authentication_execution.reset_otp_email,
        keycloak_authentication_execution.reset_otp_password,
      ] : [step.realm_id, step.parent_flow_alias, step.priority, step.authenticator, step.requirement]
      ] == [
      [keycloak_realm.main.id, "reset-credentials-otp", 10, "reset-credentials-choose-user", "REQUIRED"],
      [keycloak_realm.main.id, "reset-credentials-otp", 20, "reset-credential-email", "REQUIRED"],
      [keycloak_realm.main.id, "reset-credentials-otp", 50, "reset-password", "REQUIRED"],
    ]
    error_message = "The top-level steps must be choose-user (10), reset e-mail (20) and reset-password (50), all REQUIRED."
  }

  assert {
    condition = [
      for flow in [
        keycloak_authentication_subflow.reset_otp_check,
        keycloak_authentication_subflow.reset_otp_deny,
      ] : [flow.realm_id, flow.parent_flow_alias, flow.priority, flow.alias, flow.provider_id, flow.requirement]
      ] == [
      [keycloak_realm.main.id, "reset-credentials-otp", 30, "reset-credentials-otp-check", "basic-flow", "CONDITIONAL"],
      [keycloak_realm.main.id, "reset-credentials-otp", 40, "reset-credentials-otp-deny", "basic-flow", "CONDITIONAL"],
    ]
    error_message = "The OTP check (30) and the deny step (40) must be CONDITIONAL subflows between the reset e-mail and reset-password."
  }

  # A REQUIRED OTP form without the user-configured condition queues
  # CONFIGURE_TOTP for a user without OTP: the reset link alone then enrolls
  # a new device.
  assert {
    condition = [
      for step in [
        keycloak_authentication_execution.reset_otp_check_condition,
        keycloak_authentication_execution.reset_otp_check_form,
      ] : [step.realm_id, step.parent_flow_alias, step.priority, step.authenticator, step.requirement]
      ] == [
      [keycloak_realm.main.id, "reset-credentials-otp-check", 10, "conditional-user-configured", "REQUIRED"],
      [keycloak_realm.main.id, "reset-credentials-otp-check", 20, "auth-otp-form", "REQUIRED"],
    ]
    error_message = "The OTP check must run auth-otp-form only for a user with an OTP credential."
  }

  assert {
    condition = [
      for step in [
        keycloak_authentication_execution.reset_otp_deny_condition,
        keycloak_authentication_execution.reset_otp_deny,
      ] : [step.realm_id, step.parent_flow_alias, step.priority, step.authenticator, step.requirement]
      ] == [
      [keycloak_realm.main.id, "reset-credentials-otp-deny", 10, "conditional-credential", "REQUIRED"],
      [keycloak_realm.main.id, "reset-credentials-otp-deny", 20, "deny-access-authenticator", "REQUIRED"],
    ]
    error_message = "The deny step must deny the reset when no OTP code was used."
  }

  assert {
    condition = (
      keycloak_authentication_execution_config.reset_otp_deny_condition.realm_id == keycloak_realm.main.id &&
      keycloak_authentication_execution_config.reset_otp_deny_condition.execution_id == keycloak_authentication_execution.reset_otp_deny_condition.id &&
      keycloak_authentication_execution_config.reset_otp_deny_condition.config == tomap({ credentials = "otp", included = "false" })
    )
    error_message = "The deny condition must be true when the user did not use an otp credential in this reset."
  }

  assert {
    condition = (
      keycloak_authentication_execution_config.reset_otp_deny.realm_id == keycloak_realm.main.id &&
      keycloak_authentication_execution_config.reset_otp_deny.execution_id == keycloak_authentication_execution.reset_otp_deny.id &&
      keys(keycloak_authentication_execution_config.reset_otp_deny.config) == tolist(["denyErrorMessage"]) &&
      strcontains(keycloak_authentication_execution_config.reset_otp_deny.config.denyErrorMessage, "administrator")
    )
    error_message = "The deny step must tell a user without OTP to ask an administrator."
  }
}
