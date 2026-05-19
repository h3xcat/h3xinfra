# 10-keycloak

Keycloak identity provider for H3X Infra. Deployed via the upstream
**Keycloak Operator** (`k8s.keycloak.org/v2alpha1`) backed by a
**CloudNativePG** Postgres `Cluster`. Realm and client configuration is
managed declaratively via Terraform so OIDC clients stay in sync with
inventory.

## Layout

```
10-keycloak/
├── standup.yml                       # Installs Operator, deploys CRs, runs terraform apply
├── teardown.yml                      # Runs terraform destroy + removes Helm releases
├── charts/
│   ├── h3xinfra-keycloak-pre/        # Bootstrap admin Secret, CNPG Cluster, split HTTPRoutes
│   └── h3xinfra-keycloak-main/       # Renders the Keycloak CR (+ PDB) consumed by the Operator
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

1. **Keycloak Operator** is installed by applying the upstream manifests
   pinned to `chart_versions.keycloak` (the Operator is distributed as raw
   manifests, not a Helm chart). The operator's Deployment must reach
   `Available` before reconciliation proceeds.
2. **Pre-chart** (`h3xinfra-keycloak-pre`) creates:
   - A `Secret` (`<prefix>-pre-bootstrap-admin`) holding the master-realm
     admin username/password, consumed by the Keycloak CR via
     `spec.bootstrapAdmin.user.secret`.
   - A **CloudNativePG `Cluster`** (`postgresql.cnpg.io/v1`) backing Keycloak.
     Replaces the previous single-pod Bitnami subchart so a node failure
     does not take auth down. Keycloak connects through the CNPG-rendered
     `<cluster>-rw` Service which always points at the current primary.
   - Two `HTTPRoute` objects (Gateway API) on the same host, attached to the
     shared Envoy Gateway:
     - **Public**: `/realms/`, `/resources/`, `/js/`, `/robots.txt` — reachable
       by downstream services and end users.
     - **Admin**: `/admin/`, `/metrics`, `/health` — restricted via a
       `SecurityPolicy` (`authorization.principal.clientCIDRs`) populated
       from `keycloak.ingress.trusted_setup_ips`.
3. **Main chart** (`h3xinfra-keycloak-main`) renders a `Keycloak` CR
   (`k8s.keycloak.org/v2alpha1`). The Operator reconciles the CR into a
   StatefulSet, Service, pod anti-affinity, JGroups KUBE_PING discovery
   for the Infinispan distributed cache, and rolling upgrades. The chart
   also renders a `PodDisruptionBudget` with `minAvailable: 1` (the
   Operator does not render one itself).
4. **Readiness probe** hits `/realms/master/.well-known/openid-configuration`
   and retries until it returns `200`.
5. **Terraform apply** renders `terraform.tfvars.json` from inventory, runs
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
| `keycloak.db_password`         | Keycloak app DB user password (CNPG)        |
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


