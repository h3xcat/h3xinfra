# Terraform state lives on the NAS (mounted at /h3xinfra on the controller via
# Docker) so that runs from any workspace clone share the same state.
#
# The directory is created out-of-band before `terraform init`; the playbook
# (standup.yml) ensures it exists.
terraform {
  backend "local" {
    path = "/h3xinfra/state/keycloak/terraform.tfstate"
  }
}
