terraform {
  required_version = ">= 1.15.3"

  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "~> 5.7"
    }
  }
}

provider "keycloak" {
  client_id     = "admin-cli"
  username      = var.keycloak_admin_username
  password      = var.keycloak_admin_password
  url           = var.keycloak_url
  initial_login = false
}
