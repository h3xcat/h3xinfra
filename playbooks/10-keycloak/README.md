# 10-keycloak

Keycloak identity provider for H3X Infra. Realm and client configuration is
managed declaratively via Terraform so OIDC clients stay in sync with
inventory.

## Layout

```
10-keycloak/
├── standup.yml                       # Deploys Keycloak + runs terraform apply
├── teardown.yml                      # Runs terraform destroy + removes Helm releases
├── charts/
│   └── h3xinfra-keycloak-pre/        # Bootstrap secret + split ingress
└── terraform/
    ├── providers.tf                  # keycloak/keycloak provider
    ├── variables.tf
    ├── realm.tf                      # Single realm, SMTP settings
    ├── clients.tf                    # OIDC clients from inventory.clients[]
    ├── outputs.tf
    └── .gitignore                    # Ignores state + rendered tfvars
```

## Deployment flow

### Standup (`ansible-playbook playbooks/10-keycloak/standup.yml`)

1. **Pre-chart** (`h3xinfra-keycloak-pre`) creates:
   - A `Secret` (`<prefix>-pre-bootstrap`) with the admin username/password and
     PostgreSQL credentials, consumed by the Bitnami chart via
     `auth.existingSecret` and `postgresql.auth.existingSecret`.
   - Two `HTTPRoute` objects (Gateway API) on the same host, attached to the
     shared Envoy Gateway:
     - **Public**: `/realms/`, `/resources/`, `/js/`, `/robots.txt` — reachable
       by downstream services and end users.
     - **Admin**: `/admin/`, `/metrics`, `/health` — restricted via a
       `SecurityPolicy` (`authorization.principal.clientCIDRs`) populated
       from `keycloak.ingress.trusted_setup_ips`.
2. **Main chart** (`bitnami/keycloak`) deploys Keycloak in production mode with
   its own bundled PostgreSQL subchart. Chart-native ingress is disabled; the
   pre-chart owns routing via Gateway API.
3. **Readiness probe** hits `/realms/master/.well-known/openid-configuration`
   and retries until it returns `200`.
4. **Terraform apply** renders `terraform.tfvars.json` from inventory, runs
   `terraform init` + `apply` against the fresh instance, and creates the
   configured realm + OIDC clients.

### Teardown (`ansible-playbook playbooks/10-keycloak/teardown.yml`)

Runs in reverse order: `terraform destroy` → main Helm release → pre Helm
release → namespace.

## Configuration

All settings live in `inventory/production/host_vars/k8s/keycloak.yml`.

### Required vault-encrypted fields

Use `h3xinfra-gen-pass` to produce vault ciphertext for each:

| Field                          | Purpose                                     |
|--------------------------------|---------------------------------------------|
| `keycloak.admin.password`      | Master realm admin (also used by Terraform) |
| `keycloak.db_password`         | Keycloak app DB user password               |
| `keycloak.db_postgres_password`| PostgreSQL superuser password               |
| `keycloak.realm.smtp.password` | SMTP relay password (if SMTP enabled)       |
| `keycloak.clients[*].client_secret` | Per-client confidential OIDC secret    |

### Adding an OIDC client

Append an entry to `keycloak.clients` in the inventory file:

```yaml
keycloak:
  clients:
  - name: "openwebui"
    client_secret: !vault |
      $ANSIBLE_VAULT;1.1;AES256
      ...
    root_url: "https://chat.app.example.com"
    redirect_uris:
    - "https://chat.app.example.com/oauth/oidc/callback"
    web_origins:
    - "https://chat.app.example.com"
```

Re-run `ansible-playbook playbooks/10-keycloak/standup.yml`. Terraform will
converge only the delta. Consumers can then use:

```
oidc_issuer_url: "{{ keycloak.issuer_url_base }}/realms/{{ keycloak.realm.name }}"
```

## Terraform state

State is kept **locally** under `playbooks/10-keycloak/terraform/` and ignored
by git (see `.gitignore`). For a shared/remote backend, drop a `backend.tf`
next to the other `.tf` files — the `force_init: true` on the Ansible
terraform module will re-init on the next run.

The `terraform.tfvars.json` file is rendered fresh every playbook invocation
and contains plaintext secrets; keep it local only (already git-ignored).


