resource "keycloak_realm" "main" {
  realm        = var.realm_name
  enabled      = true
  display_name = var.realm_display_name != "" ? var.realm_display_name : var.realm_name

  # Sensible defaults for a small self-hosted deployment.
  login_with_email_allowed = true
  registration_allowed     = false
  reset_password_allowed   = true
  remember_me              = true
  verify_email             = true
  duplicate_emails_allowed = false
  edit_username_allowed    = false

  password_policy = "upperCase(1) and length(12) and forceExpiredPasswordChange(365) and notUsername"

  # Brute-force detection. Keycloak counts failed logins per user (not per
  # client IP) and locks the user temporarily. Permanent lockout stays off so
  # that failed logins by a third party cannot disable an account until an
  # admin unlocks it. A successful login resets the count.
  #
  # Failed OTP codes also count as failed logins. max_secondary_auth_failures
  # is not set. On Keycloak 26.6.0 and later the provider sends 0, which means
  # that there is no separate OTP lockout.
  #
  # Headers are not set here. When `headers` is absent, the provider sets its
  # built-in default browser security headers, the same as it did before this
  # block was added.
  #
  # This block applies only to this realm. The master realm is not managed by
  # this Terraform and does not get brute-force detection from it.
  security_defenses {
    brute_force_detection {
      permanent_lockout                = false
      max_temporary_lockouts           = 0 # never escalate to a permanent lockout
      brute_force_strategy             = "MULTIPLE"
      max_login_failures               = 5
      wait_increment_seconds           = 60    # wait = 60s x (failures / 5): grows every 5 failures
      max_failure_wait_seconds         = 900   # cap: 15m (reached at 75 failures)
      failure_reset_time_seconds       = 43200 # failure count resets after 12h
      quick_login_check_milli_seconds  = 1000  # two failures < 1s apart ...
      minimum_quick_login_wait_seconds = 60    # ... lock the user for 1m
    }
  }

  # Session lifetimes — extended from Keycloak defaults (30m idle / 10h max)
  # so users aren't logged out frequently on this self-hosted deployment.
  sso_session_idle_timeout             = "12h"
  sso_session_max_lifespan             = "168h"  # 7d
  sso_session_idle_timeout_remember_me = "720h"  # 30d
  sso_session_max_lifespan_remember_me = "720h"  # 30d
  offline_session_idle_timeout         = "720h"  # 30d
  offline_session_max_lifespan         = "1440h" # 60d

  dynamic "smtp_server" {
    for_each = var.smtp.enabled ? [var.smtp] : []
    content {
      host                  = smtp_server.value.host
      port                  = tostring(smtp_server.value.port)
      from                  = smtp_server.value.from
      from_display_name     = smtp_server.value.from_display_name
      ssl                   = smtp_server.value.ssl
      starttls              = !smtp_server.value.ssl
      reply_to              = smtp_server.value.from
      reply_to_display_name = smtp_server.value.from_display_name

      dynamic "auth" {
        for_each = smtp_server.value.auth_enabled ? [1] : []
        content {
          username = smtp_server.value.username
          password = smtp_server.value.password
        }
      }
    }
  }
}
