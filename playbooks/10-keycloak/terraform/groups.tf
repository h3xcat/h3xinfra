# =============================================================================
# Realm groups from inventory (keycloak.groups).
#
# Each group gets the client roles that it lists. The role list is
# authoritative (exhaustive): Terraform removes every other role mapping of a
# declared group, also mappings that someone added in the admin console.
#
# Memberships are additive: Terraform adds each listed user to the group and
# keeps every other group membership of the user (for example k8s-admins,
# which is assigned in the admin console). When a user is removed from
# `members`, Terraform removes only that membership.
#
# The users must already exist and be enabled. Terraform does not create
# users. An unknown username fails the run before any change.
# =============================================================================

locals {
  groups_by_name = { for group in var.groups : group.name => group }

  # Every username listed in a group, mapped to the names of its groups. One
  # keycloak_user_groups resource per user keeps two resources from managing
  # the same user.
  group_member_groups = {
    for username in distinct(flatten([for group in var.groups : group.members])) :
    username => [for group in var.groups : group.name if contains(group.members, username)]
  }
}

resource "keycloak_group" "groups" {
  for_each = local.groups_by_name

  realm_id = keycloak_realm.main.id
  name     = each.key
}

# One resource per declared group, also when the group lists no roles: then
# the group must have no role mappings.
resource "keycloak_group_roles" "groups" {
  for_each = local.groups_by_name

  realm_id = keycloak_realm.main.id
  group_id = keycloak_group.groups[each.key].id
  # The variable validation makes sure that each role is in
  # keycloak.clients[].client_roles, so keycloak_role.client_roles has it.
  role_ids = flatten([
    for client, roles in each.value.client_roles : [
      for role in roles : keycloak_role.client_roles["${client}:${role}"].id
    ]
  ])
  exhaustive = true
}

# Exact username lookup. The provider fails with "user with username <name>
# not found" when the user does not exist. Terraform reads this data source
# during plan when the realm already exists.
data "keycloak_user" "group_members" {
  for_each = local.group_member_groups

  realm_id = keycloak_realm.main.id
  username = each.key

  lifecycle {
    postcondition {
      condition     = self.username == each.key && self.enabled
      error_message = "keycloak.groups lists the member \"${each.key}\", but the realm has no enabled user with exactly this username."
    }
  }
}

# exhaustive = false: add only the listed groups. Other memberships of the
# user stay. On delete, the provider removes only the listed groups.
resource "keycloak_user_groups" "group_members" {
  for_each = local.group_member_groups

  realm_id   = keycloak_realm.main.id
  user_id    = data.keycloak_user.group_members[each.key].id
  group_ids  = [for name in each.value : keycloak_group.groups[name].id]
  exhaustive = false
}

output "group_ids" {
  description = "Map of group name (keycloak.groups) -> Keycloak group id."
  value       = { for name, group in keycloak_group.groups : name => group.id }
}
