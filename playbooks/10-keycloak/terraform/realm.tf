resource "keycloak_realm" "main" {
  realm        = var.realm_name
  enabled      = true
  display_name = var.realm_display_name != "" ? var.realm_display_name : var.realm_name

  # Sensible defaults for a small self-hosted deployment.
  login_with_email_allowed   = true
  registration_allowed       = false
  reset_password_allowed     = true
  remember_me                = true
  verify_email               = true
  duplicate_emails_allowed   = false
  edit_username_allowed      = false

  password_policy = "upperCase(1) and length(12) and forceExpiredPasswordChange(365) and notUsername"

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
