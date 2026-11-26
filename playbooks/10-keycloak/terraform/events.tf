# Events of the application realm. The jboss-logging listener writes each
# error event as a WARN line to the Keycloak log. Loki alert rules in the
# private repository read these lines. The database stores no events. See
# README.md, section "Password reset".

resource "keycloak_realm_events" "main" {
  realm_id = keycloak_realm.main.id

  events_listeners = ["jboss-logging"]

  # No login events and no admin events in the database.
  events_enabled       = false
  admin_events_enabled = false
}
