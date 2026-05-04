terraform {
  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "~> 5.0"
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
# Custom browser authentication flow that REQUIRES MFA but lets the user
# choose between WebAuthn (security key / passkey) and TOTP at sign-in.
#
# Flow layout:
#   browser-mfa
#     ├── auth-cookie                                  ALTERNATIVE
#     ├── identity-provider-redirector                 ALTERNATIVE
#     └── browser-mfa-forms (subflow)                  ALTERNATIVE
#           ├── auth-username-password-form            REQUIRED
#           └── browser-mfa-2fa (subflow)              REQUIRED
#                 ├── webauthn-authenticator           ALTERNATIVE
#                 └── auth-otp-form                    ALTERNATIVE
#
# Both 2FA executions are ALTERNATIVE inside a REQUIRED subflow, so Keycloak
# presents whichever credential the user has and renders a "Try Another Way"
# link to switch between any registered second factor.
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
# - If the user has none, the subflow is skipped here and the
#   CONFIGURE_TOTP / webauthn-register required actions (queued as defaults
#   below) walk them through enrollment immediately after password auth.
#   On the next login the condition will match and 2FA becomes mandatory.

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
# Enable TOTP and WebAuthn registration so they're available; do NOT mark
# them as default actions because Keycloak then evaluates them at every
# direct-grant login and rejects accounts (e.g. break-glass admin) that
# don't have those credentials configured. Instead, enrollment is queued
# per-user (e.g. for `exampleuser`) by adding the action to user_required_action.

resource "keycloak_required_action" "configure_totp" {
  realm_id       = var.realm_id
  alias          = "CONFIGURE_TOTP"
  name           = "Configure OTP"
  enabled        = true
  default_action = false
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
# action on the master realm, which causes direct-grant logins (used by the
# Terraform admin client and by break-glass automation) to fail with
# "Account is not fully set up". Force it to non-default so existing accounts
# aren't blocked; users can still enroll a passwordless credential via the
# account console on demand.
resource "keycloak_required_action" "webauthn_register_passwordless" {
  realm_id       = var.realm_id
  alias          = "webauthn-register-passwordless"
  name           = "Webauthn Register Passwordless"
  enabled        = true
  default_action = false
  priority       = 30
}
