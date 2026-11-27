# Mocked-provider tests for the realm events (terraform/events.tf).
# test_groups.py copies the module files and this file to a temporary
# directory and runs `terraform test` there. No Keycloak is contacted.

mock_provider "keycloak" {}

variables {
  keycloak_url            = "https://keycloak.example.invalid"
  keycloak_admin_username = "test"
  keycloak_admin_password = "FAKE_TEST_PASSWORD"
  realm_name              = "test"
}

run "realm_logs_error_events_and_stores_none" {
  # apply (mocked): the realm id is only known after apply.
  command = apply

  assert {
    condition     = keycloak_realm_events.main.realm_id == keycloak_realm.main.id
    error_message = "The events configuration must belong to the application realm."
  }

  # The Loki alerts read the log lines of this listener.
  assert {
    condition     = keycloak_realm_events.main.events_listeners == toset(["jboss-logging"])
    error_message = "The application realm must send its events to the jboss-logging listener only."
  }

  # Listeners get every event without the event store. enabled_event_types
  # filters only the store.
  assert {
    condition = (
      keycloak_realm_events.main.events_enabled == false &&
      keycloak_realm_events.main.admin_events_enabled == false &&
      keycloak_realm_events.main.enabled_event_types == null
    )
    error_message = "The database must store no login events and no admin events."
  }
}
