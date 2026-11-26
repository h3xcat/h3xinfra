# -----------------------------------------------------------------------------
# Reset-credentials flow of the application realm. After the user opens the
# reset e-mail link, the user must give a valid OTP code. Only then can the
# user set a new password.
#
# Keycloak sends the reset link through the realm SMTP server. The built-in
# "reset credentials" flow trusts that e-mail alone, and its "Reset OTP" step
# makes the user enroll a new OTP device. Thus, a person who can read or
# redirect the realm e-mail can take the account. This flow also needs the
# current OTP device of the user. It never removes or replaces an OTP
# credential.
#
# Flow layout (priority in the last column):
#   reset-credentials-otp
#     ├── reset-credentials-choose-user               REQUIRED     10
#     ├── reset-credential-email                      REQUIRED     20
#     ├── reset-credentials-otp-check (subflow)       CONDITIONAL  30
#     │     ├── conditional-user-configured           REQUIRED     10
#     │     └── auth-otp-form                         REQUIRED     20
#     ├── reset-credentials-otp-deny (subflow)        CONDITIONAL  40
#     │     ├── conditional-credential                REQUIRED     10
#     │     └── deny-access-authenticator             REQUIRED     20
#     └── reset-password                              REQUIRED     50
#
# - The check subflow runs only for a user with an OTP credential. That user
#   must give a valid code. Failed codes count for the brute-force detection
#   of the realm (realm.tf).
# - The deny subflow runs when the user did not give a valid OTP code in this
#   reset (credential type "otp" not used). A user without an OTP credential
#   (also a user with only WebAuthn) thus gets the deny message and no new
#   password. An admin resets the password of that user (README.md,
#   "Password reset").
# - auth-otp-form is REQUIRED only behind the user-configured condition. A
#   REQUIRED OTP step for a user without OTP queues CONFIGURE_TOTP. The
#   holder of the reset link can then enroll an own device.
# - The deny condition is true also without its configuration. Thus, a
#   configuration that is not there denies the reset. A CONDITIONAL subflow
#   without a condition step does not run. Thus, the binding waits for all
#   steps (mfa.tf).
#
# Every step has an explicit priority. On Keycloak 25 and later the priority
# sets the order of the steps, and the provider sends priority 0 for a step
# without one.
#
# The MFA module (mfa.tf) binds this flow as the reset-credentials flow of the
# realm, because the bindings resource is realm-wide.
# -----------------------------------------------------------------------------

resource "keycloak_authentication_flow" "reset_otp" {
  realm_id    = keycloak_realm.main.id
  alias       = "reset-credentials-otp"
  provider_id = "basic-flow"
  description = "Reset the password after the reset e-mail and an OTP code. Users without OTP get an admin reset."
}

resource "keycloak_authentication_execution" "reset_otp_choose_user" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_flow.reset_otp.alias
  authenticator     = "reset-credentials-choose-user"
  requirement       = "REQUIRED"
  priority          = 10
}

resource "keycloak_authentication_execution" "reset_otp_email" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_flow.reset_otp.alias
  authenticator     = "reset-credential-email"
  requirement       = "REQUIRED"
  priority          = 20
}

resource "keycloak_authentication_subflow" "reset_otp_check" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_flow.reset_otp.alias
  alias             = "reset-credentials-otp-check"
  provider_id       = "basic-flow"
  description       = "Ask for the OTP code of a user with an OTP credential."
  requirement       = "CONDITIONAL"
  priority          = 30
}

resource "keycloak_authentication_execution" "reset_otp_check_condition" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_subflow.reset_otp_check.alias
  authenticator     = "conditional-user-configured"
  requirement       = "REQUIRED"
  priority          = 10
}

resource "keycloak_authentication_execution" "reset_otp_check_form" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_subflow.reset_otp_check.alias
  authenticator     = "auth-otp-form"
  requirement       = "REQUIRED"
  priority          = 20
}

resource "keycloak_authentication_subflow" "reset_otp_deny" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_flow.reset_otp.alias
  alias             = "reset-credentials-otp-deny"
  provider_id       = "basic-flow"
  description       = "Deny the reset when the OTP check did not succeed."
  requirement       = "CONDITIONAL"
  priority          = 40
}

resource "keycloak_authentication_execution" "reset_otp_deny_condition" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_subflow.reset_otp_deny.alias
  authenticator     = "conditional-credential"
  requirement       = "REQUIRED"
  priority          = 10
}

# True when the user did not use an "otp" credential in this reset.
resource "keycloak_authentication_execution_config" "reset_otp_deny_condition" {
  realm_id     = keycloak_realm.main.id
  execution_id = keycloak_authentication_execution.reset_otp_deny_condition.id
  alias        = "reset-credentials-otp-not-used"
  config = {
    credentials = "otp"
    included    = "false"
  }
}

resource "keycloak_authentication_execution" "reset_otp_deny" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_subflow.reset_otp_deny.alias
  authenticator     = "deny-access-authenticator"
  requirement       = "REQUIRED"
  priority          = 20
}

resource "keycloak_authentication_execution_config" "reset_otp_deny" {
  realm_id     = keycloak_realm.main.id
  execution_id = keycloak_authentication_execution.reset_otp_deny.id
  alias        = "reset-credentials-otp-deny-message"
  config = {
    denyErrorMessage = "Your account has no OTP device. Ask an administrator to reset your password."
  }
}

resource "keycloak_authentication_execution" "reset_otp_password" {
  realm_id          = keycloak_realm.main.id
  parent_flow_alias = keycloak_authentication_flow.reset_otp.alias
  authenticator     = "reset-password"
  requirement       = "REQUIRED"
  priority          = 50
}
