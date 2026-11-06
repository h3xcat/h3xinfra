terraform {
  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "~> 5.10"
    }
  }
}

variable "realm_id" {
  description = "Realm id to apply the MFA browser flow to."
  type        = string
}

variable "flow_alias" {
  description = "Alias for the custom browser flow."
  type        = string
  default     = "browser-mfa"
}

# -----------------------------------------------------------------------------
# Custom browser authentication flow with a second factor. At sign-in, the
# user can select WebAuthn (security key or passkey) or TOTP.
#
# Flow layout:
#   browser-mfa
#     ├── auth-cookie                                  ALTERNATIVE
#     ├── identity-provider-redirector                 ALTERNATIVE
#     └── browser-mfa-forms (subflow)                  ALTERNATIVE
#           ├── auth-username-password-form            REQUIRED
#           └── browser-mfa-2fa (subflow)              CONDITIONAL
#                 ├── conditional-user-configured      REQUIRED
#                 ├── webauthn-authenticator           ALTERNATIVE
#                 └── auth-otp-form                    ALTERNATIVE
#
# The 2FA subflow is CONDITIONAL. It runs only when the user has an otp or
# webauthn credential. Then the second factor is mandatory. The two 2FA
# executions are ALTERNATIVE. Keycloak shows the credential that the user
# has and a "Try Another Way" link to change to a different second factor.
#
# A user without a second factor must have a pending CONFIGURE_TOTP action.
# Keycloak adds this action to each new user (see "Required actions" below).
# -----------------------------------------------------------------------------

resource "keycloak_authentication_flow" "browser_mfa" {
  realm_id    = var.realm_id
  alias       = var.flow_alias
  provider_id = "basic-flow"
  description = "Browser flow with mandatory MFA (WebAuthn or OTP)."
}

resource "keycloak_authentication_execution" "cookie" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_flow.browser_mfa.alias
  authenticator     = "auth-cookie"
  requirement       = "ALTERNATIVE"
}

resource "keycloak_authentication_execution" "idp_redirector" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_flow.browser_mfa.alias
  authenticator     = "identity-provider-redirector"
  requirement       = "ALTERNATIVE"

  depends_on = [keycloak_authentication_execution.cookie]
}

resource "keycloak_authentication_subflow" "forms" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_flow.browser_mfa.alias
  alias             = "${var.flow_alias}-forms"
  provider_id       = "basic-flow"
  requirement       = "ALTERNATIVE"

  depends_on = [keycloak_authentication_execution.idp_redirector]
}

resource "keycloak_authentication_execution" "username_password" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_subflow.forms.alias
  authenticator     = "auth-username-password-form"
  requirement       = "REQUIRED"
}

# 2FA subflow is CONDITIONAL and guarded by `conditional-user-configured`.
# - If the user has at least one of the listed credentials (otp/webauthn),
#   the subflow runs and forces them to use one of them (with "Try Another
#   Way" to switch).
# - If the user has none, the subflow is skipped here. The CONFIGURE_TOTP
#   required action then makes the user enroll an OTP device immediately
#   after password auth. On the next login the condition matches and 2FA
#   is mandatory.
# CONFIGURE_TOTP is a default action (see below), so Keycloak adds it to
# every user that it creates. Keycloak does not add default actions to
# users that already exist. Queue CONFIGURE_TOTP per user for an existing
# user without a second factor, or that user can log in with only a password.

resource "keycloak_authentication_subflow" "twofa" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_subflow.forms.alias
  alias             = "${var.flow_alias}-2fa"
  provider_id       = "basic-flow"
  requirement       = "CONDITIONAL"

  depends_on = [keycloak_authentication_execution.username_password]
}

resource "keycloak_authentication_execution" "twofa_condition" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_subflow.twofa.alias
  authenticator     = "conditional-user-configured"
  requirement       = "REQUIRED"
}

resource "keycloak_authentication_execution" "webauthn" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_subflow.twofa.alias
  authenticator     = "webauthn-authenticator"
  requirement       = "ALTERNATIVE"

  depends_on = [keycloak_authentication_execution.twofa_condition]
}

resource "keycloak_authentication_execution" "otp" {
  realm_id          = var.realm_id
  parent_flow_alias = keycloak_authentication_subflow.twofa.alias
  authenticator     = "auth-otp-form"
  requirement       = "ALTERNATIVE"

  depends_on = [keycloak_authentication_execution.webauthn]
}

resource "keycloak_authentication_bindings" "browser_mfa" {
  realm_id     = var.realm_id
  browser_flow = keycloak_authentication_flow.browser_mfa.alias
}

# Required actions ------------------------------------------------------------
#
# Enable TOTP and WebAuthn registration so they're available.
#
# CONFIGURE_TOTP is a default action: Keycloak adds it to each new user when
# it creates the user (admin API, bootstrap admin, IdP first login). This
# makes second-factor enrollment mandatory for new users. TOTP is the
# default, not WebAuthn, because:
# - it works with any authenticator app on any browser or device;
# - its credential does not depend on the WebAuthn RP ID (the hostname);
# - only one default action means a new user enrolls one factor, not two.
# A user can add WebAuthn later in the account console.
#
# Automation accounts:
# - Existing users (the break-glass `admin` that Terraform and Ansible use
#   for direct-grant login) do not change. Keycloak applies default actions
#   only at user creation.
# - Service-account users (client_credentials grant) are not affected. That
#   grant does not check required actions.
# - A direct-grant (password grant) login fails with "Account is not fully
#   set up" while the user has a pending required action. If you create a new
#   user for direct-grant automation, remove CONFIGURE_TOTP from that user.

resource "keycloak_required_action" "configure_totp" {
  realm_id       = var.realm_id
  alias          = "CONFIGURE_TOTP"
  name           = "Configure OTP"
  enabled        = true
  default_action = true
  priority       = 10
}

resource "keycloak_required_action" "webauthn_register" {
  realm_id       = var.realm_id
  alias          = "webauthn-register"
  name           = "Webauthn Register"
  enabled        = true
  default_action = false
  priority       = 20
}

# Keycloak 26 ships `webauthn-register-passwordless` enabled and as a default
# action on the master realm. A user with this pending action cannot use a
# direct-grant login (the Terraform admin client and break-glass automation
# use it). The login fails with "Account is not fully set up".
# default_action = false stops Keycloak from adding this action to new users,
# for example a recreated bootstrap admin. It does not remove the action from
# users that exist. Users can enroll a passwordless credential in the account
# console.
resource "keycloak_required_action" "webauthn_register_passwordless" {
  realm_id       = var.realm_id
  alias          = "webauthn-register-passwordless"
  name           = "Webauthn Register Passwordless"
  enabled        = true
  default_action = false
  priority       = 30
}
