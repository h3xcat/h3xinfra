# Mocked-provider tests for keycloak.groups (terraform/groups.tf).
# test_groups.py copies the module files and this file to a temporary
# directory and runs `terraform test` there. No Keycloak is contacted.

mock_provider "keycloak" {
  # The data source returns an enabled user unless a run overrides it.
  mock_data "keycloak_user" {
    defaults = {
      enabled = true
    }
  }
}

variables {
  keycloak_url            = "https://keycloak.example.invalid"
  keycloak_admin_username = "test"
  keycloak_admin_password = "FAKE_TEST_PASSWORD"
  realm_name              = "test"
  clients = [
    {
      name          = "reports-cms"
      client_secret = "FAKE_REPORTS_SECRET"
      redirect_uris = ["https://reports.example.com/callback"]
      client_roles  = ["report-editor", "report-publisher"]
    },
    {
      name          = "openwebui"
      client_secret = "FAKE_OPENWEBUI_SECRET"
      redirect_uris = ["https://chat.example.com/callback"]
      client_roles  = ["admin", "user"]
    },
  ]
}

run "no_groups_by_default" {
  command = plan
  assert {
    condition = (
      length(keycloak_group.groups) == 0 &&
      length(keycloak_group_roles.groups) == 0 &&
      length(keycloak_user_groups.group_members) == 0 &&
      length(data.keycloak_user.group_members) == 0
    )
    error_message = "Without groups the layer must not plan any group, role mapping or membership."
  }
}

run "groups_roles_and_additive_memberships" {
  # apply (mocked): ids and the data source are only known after apply.
  command = apply
  variables {
    groups = [
      { name = "report-editors", client_roles = { "reports-cms" = ["report-editor"] } },
      { name = "report-publishers", client_roles = { "reports-cms" = ["report-publisher"] }, members = ["alice"] },
      { name = "chat-admins", client_roles = { "openwebui" = ["admin"], "reports-cms" = ["report-editor"] }, members = ["alice", "bob"] },
      { name = "plain" },
    ]
  }
  assert {
    condition = (
      toset(keys(keycloak_group.groups)) == toset(["report-editors", "report-publishers", "chat-admins", "plain"]) &&
      alltrue([for name, group in keycloak_group.groups : group.name == name && group.realm_id == keycloak_realm.main.id && group.parent_id == null])
    )
    error_message = "Each declared group must be a top-level group in the managed realm."
  }
  assert {
    condition = (
      toset(keys(keycloak_group_roles.groups)) == toset(keys(keycloak_group.groups)) &&
      alltrue([for name, mapping in keycloak_group_roles.groups : mapping.exhaustive && mapping.group_id == keycloak_group.groups[name].id])
    )
    error_message = "Every declared group, also one without roles, must get one exhaustive role mapping."
  }
  assert {
    condition = (
      keycloak_group_roles.groups["report-editors"].role_ids == toset([keycloak_role.client_roles["reports-cms:report-editor"].id]) &&
      keycloak_group_roles.groups["report-publishers"].role_ids == toset([keycloak_role.client_roles["reports-cms:report-publisher"].id]) &&
      keycloak_group_roles.groups["chat-admins"].role_ids == toset([
        keycloak_role.client_roles["openwebui:admin"].id,
        keycloak_role.client_roles["reports-cms:report-editor"].id,
      ]) &&
      length(keycloak_group_roles.groups["plain"].role_ids) == 0
    )
    error_message = "Each group must map exactly the client roles that it lists."
  }
  assert {
    condition = (
      toset(keys(data.keycloak_user.group_members)) == toset(["alice", "bob"]) &&
      alltrue([for name, user in data.keycloak_user.group_members : user.username == name && user.realm_id == keycloak_realm.main.id])
    )
    error_message = "Each member must be looked up once by exact username in the managed realm."
  }
  assert {
    condition = (
      toset(keys(keycloak_user_groups.group_members)) == toset(["alice", "bob"]) &&
      alltrue([for name, membership in keycloak_user_groups.group_members : (
        !membership.exhaustive && membership.user_id == data.keycloak_user.group_members[name].id
      )])
    )
    error_message = "Memberships must be one additive (non-exhaustive) resource per user."
  }
  assert {
    condition = (
      keycloak_user_groups.group_members["alice"].group_ids == toset([keycloak_group.groups["report-publishers"].id, keycloak_group.groups["chat-admins"].id]) &&
      keycloak_user_groups.group_members["bob"].group_ids == toset([keycloak_group.groups["chat-admins"].id])
    )
    error_message = "A user must be added to exactly the declared groups that list the user."
  }
  assert {
    condition     = output.group_ids == { for name, group in keycloak_group.groups : name => group.id }
    error_message = "group_ids must map every declared group name to its id."
  }
}

# Runs after the apply above, so the realm id is known and Terraform reads
# the user during plan, as in a deployment with an existing realm.
run "reject_disabled_member" {
  command = plan
  variables {
    groups = [{ name = "report-publishers", client_roles = { "reports-cms" = ["report-publisher"] }, members = ["alice"] }]
  }
  override_data {
    target = data.keycloak_user.group_members["alice"]
    values = {
      username = "alice"
      enabled  = false
    }
  }
  expect_failures = [data.keycloak_user.group_members["alice"]]
}

run "reject_duplicate_group_names" {
  command = plan
  variables {
    groups = [{ name = "report-editors" }, { name = "report-editors" }]
  }
  expect_failures = [var.groups]
}

run "reject_reserved_k8s_group" {
  command = plan
  variables {
    groups = [{ name = "k8s-admins", members = ["alice"] }]
  }
  expect_failures = [var.groups]
}

run "reject_subgroup_path" {
  command = plan
  variables {
    groups = [{ name = "apps/report-editors" }]
  }
  expect_failures = [var.groups]
}

run "reject_unknown_client" {
  command = plan
  variables {
    groups = [{ name = "report-editors", client_roles = { "missing-client" = [] } }]
  }
  expect_failures = [var.groups]
}

run "reject_unknown_role" {
  command = plan
  variables {
    groups = [{ name = "report-editors", client_roles = { "reports-cms" = ["report-admin"] } }]
  }
  expect_failures = [var.groups]
}

run "reject_role_of_other_client" {
  command = plan
  variables {
    groups = [{ name = "report-editors", client_roles = { "reports-cms" = ["admin"] } }]
  }
  expect_failures = [var.groups]
}

run "reject_duplicate_role" {
  command = plan
  variables {
    groups = [{ name = "report-editors", client_roles = { "reports-cms" = ["report-editor", "report-editor"] } }]
  }
  expect_failures = [var.groups]
}

run "reject_duplicate_member" {
  command = plan
  variables {
    groups = [{ name = "report-editors", members = ["alice", "alice"] }]
  }
  expect_failures = [var.groups]
}

run "reject_non_canonical_username" {
  command = plan
  variables {
    groups = [{ name = "report-editors", members = ["Alice"] }]
  }
  expect_failures = [var.groups]
}
