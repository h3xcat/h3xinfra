# 10-keycloak

This layer is the Keycloak identity provider for H3X Infra. The upstream
**Keycloak Operator** (`k8s.keycloak.org/v2alpha1`) deploys Keycloak. A
**CloudNativePG** Postgres `Cluster` is the backend. Terraform manages the realm and client configuration
declaratively. Thus, the OIDC clients stay in sync with the
inventory.

## Layout

```
10-keycloak/
├── standup.yml                       # Installs Operator, deploys CRs, runs terraform apply
├── teardown.yml                      # Runs terraform destroy + removes Helm releases
├── charts/
│   ├── h3xinfra-keycloak-pre/        # Bootstrap admin Secret, CNPG Cluster, HTTPRoute with named rules
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

The standup playbook does these steps:

1. **Keycloak Operator**: The playbook applies the upstream manifests
   pinned to `chart_versions.keycloak`. The Operator is distributed as raw
   manifests, not as a Helm chart. The Deployment of the operator must reach
   `Available` before the reconciliation continues.
2. **Pre-chart** (`h3xinfra-keycloak-pre`) creates these items:
   - A `Secret` (`<prefix>-pre-bootstrap-admin`). It holds the master-realm
     admin username/password. The Keycloak CR uses this Secret through
     `spec.bootstrapAdmin.user.secret`.
   - A **CloudNativePG `Cluster`** (`postgresql.cnpg.io/v1`). It is the backend of Keycloak.
     It replaces the previous single-pod Bitnami subchart, so a node failure
     does not stop auth. Keycloak connects through the CNPG-rendered
     `<cluster>-rw` Service. This Service always points to the current primary.
   - One `HTTPRoute` (Gateway API) with named rules. It attaches only to the
     shared Envoy Gateway HTTPS listener. These are the rules:
     - **Public**: `/realms/`, `/resources/`, `/js/`, `/robots.txt` — downstream
       services and end users can reach these paths.
     - **Admin**: `/admin/`, `/metrics`, `/health` — a
       `SecurityPolicy` (`authorization.principal.clientCIDRs`) restricts these paths.
       The values of the policy come from `keycloak.ingress.trusted_setup_ips`. The policy targets only the
       `admin` rule. An empty allowlist denies admin access. This configuration requires
       Envoy Gateway 1.9 rule-level policy attachment (the pinned version).
3. **Main chart** (`h3xinfra-keycloak-main`) renders a `Keycloak` CR
   (`k8s.keycloak.org/v2alpha1`). The Operator reconciles the CR into these items:
   a StatefulSet, a Service, pod anti-affinity, JGroups KUBE_PING discovery
   for the Infinispan distributed cache, and rolling upgrades. The chart
   also renders a `PodDisruptionBudget` with `minAvailable: 1`. The
   Operator does not render one.
4. **Readiness probe**: The probe sends requests to `/realms/master/.well-known/openid-configuration`.
   It tries again until it gets `200`.
5. **Terraform apply**: The playbook renders `terraform.tfvars.json` from the inventory. It runs
   `terraform init` + `apply` against the new instance. This creates the
   configured realm + OIDC clients.

### Teardown (`ansible-playbook playbooks/10-keycloak/teardown.yml`)

The teardown playbook removes the items in reverse order: `terraform destroy` → main Helm release → pre Helm
release → namespace.

## Configuration

All settings are in `inventory/production/host_vars/k8s/keycloak.yml`.

### Required vault-encrypted fields

Use `h3xinfra-gen-pass` to make the vault ciphertext for each field:

| Field                          | Purpose                                     |
|--------------------------------|---------------------------------------------|
| `keycloak.admin.password`      | Master realm admin (also used by Terraform) |
| `keycloak.db_password`         | Keycloak app DB user password (CNPG)        |
| `keycloak.realm.smtp.password` | SMTP relay password (if SMTP enabled)       |
| `keycloak.clients[*].client_secret` | Per-client confidential OIDC secret    |

### Adding an OIDC client

For native Argo CD SSO, use the [Argo CD client example](../10-argocd/README.md).

The default for clients is `access_type: CONFIDENTIAL`. Confidential clients require a vault-encrypted
`client_secret`. Browser/CLI clients can opt into `access_type: PUBLIC` with
`pkce_code_challenge_method: S256`. Public clients must not have a `client_secret`.

If you set `groups_claim: true` on a client, the layer adds a group membership mapper.
The mapper puts short group names in ID tokens, access tokens and userinfo. The default value is
false, and this setting does not change existing clients. With short names, subgroups that have the same name
under different parents give the same group name. Thus, use unique names for RBAC bindings.

To add a client, do these steps:

1. Append an entry to `keycloak.clients` in the inventory file:

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

2. Run `ansible-playbook playbooks/10-keycloak/standup.yml` again. Terraform
   converges only the delta.

Consumers can then use this value:

```
oidc_issuer_url: "{{ keycloak.issuer_url_base }}/realms/{{ keycloak.realm.name }}"
```

## Terraform state

The state is **local**, under `playbooks/10-keycloak/terraform/`. Git ignores
the state (see `.gitignore`). For a shared/remote backend, put a `backend.tf`
next to the other `.tf` files. The Ansible terraform module has `force_init: true`,
so it does the init again on the next run.

The playbook renders a new `terraform.tfvars.json` file at each run.

The file contains plaintext secrets. Keep `terraform.tfvars.json` only on the local machine. Git already ignores it.


