# Apply the MFA browser flow to the application realm.
module "mfa_h3xinfra" {
  source   = "./modules/mfa-flow"
  realm_id = keycloak_realm.main.id
}

# Apply the same MFA browser flow to the master realm so administrative
# logins are also protected. The master realm is built-in and not managed
# as a keycloak_realm resource — referencing it by id is sufficient.
module "mfa_master" {
  source   = "./modules/mfa-flow"
  realm_id = "master"
}
