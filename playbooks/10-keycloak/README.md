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
   - One `HTTPRoute` (Gateway API) with named rules, attached only to the
     shared Envoy Gateway HTTPS listener:
     - **Public**: `/realms/`, `/resources/`, `/js/`, `/robots.txt` — reachable
       by downstream services and end users.
     - **Admin**: `/admin/`, `/metrics`, `/health` — restricted via a
       `SecurityPolicy` (`authorization.principal.clientCIDRs`) populated
       from `keycloak.ingress.trusted_setup_ips`. The policy targets only the
       `admin` rule; an empty allowlist denies admin access. This requires
       Envoy Gateway 1.9 rule-level policy attachment (the pinned version).
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

### Teardown (`ansible-playbook playbooks/10-keycloak/teardown.yml -e confirm_destroy=yes-destroy-data`)

The playbook stops at the start unless you give
`-e confirm_destroy=yes-destroy-data`. The teardown deletes the realm, the
CNPG PVCs, and the namespace. You cannot recover this data.

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

For native Argo CD SSO, use the [Argo CD client example](../10-argocd/README.md).
Clients default to `access_type: CONFIDENTIAL` and require a vault-encrypted
`client_secret`. Browser/CLI clients can opt into `access_type: PUBLIC` with
`pkce_code_challenge_method: S256` and must omit `client_secret`.
Setting `groups_claim: true` on a client adds a group membership mapper with
short group names in ID tokens, access tokens and userinfo. It defaults to
false and does not change existing clients. Short names collapse same-named
subgroups under different parents, so use unique names for RBAC bindings.

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

If you do not set `web_origins`, a new client gets no CORS origins. To allow
the origins of all `redirect_uris`, set `web_origins: ["+"]`. Terraform
rejects `"*"`.

Re-run `ansible-playbook playbooks/10-keycloak/standup.yml`. Terraform will
converge only the delta. Consumers can then use:

```
oidc_issuer_url: "{{ keycloak.issuer_url_base }}/realms/{{ keycloak.realm.name }}"
```

### Proxy headers

The main chart sets the Keycloak `proxy-trusted-addresses` option to the pod
CIDRs (`cluster_cidr.ipv4` and `cluster_cidr.ipv6`). The Envoy pods connect
from these CIDRs. Keycloak ignores `X-Forwarded-*` headers from all other
sources. To trust all sources again, set `keycloak.proxy_trusted_addresses: []`.
All other pods also use these CIDRs. Host traffic can also use them (the
`cilium_host` address). Thus the main controls are the gateway header removal
and the network policy.

The shared gateway removes the client copies of these headers before Envoy
sets its own values (`ClientTrafficPolicy` in `h3xinfra-gateway-pre`). To
turn off this policy, set `gateway.client_traffic_policy.enabled: false` and
run `playbooks/07-gateway/standup.yml`.

After the deploy, the playbook waits until the StatefulSet replaces all
Keycloak pods. Then it sends `3 * keycloak.replicas` requests to
`/realms/master/.well-known/openid-configuration` and reads the issuer from
each response. Envoy spreads these requests over the pods, so usually each
pod answers some of them. If Keycloak does not trust the Envoy pods,
the issuer starts with `http://` and the playbook stops.

### Network policy

The main chart can render a `CiliumNetworkPolicy` for the Keycloak pods and a
second one for the CNPG database pods. These policies are off by default.
When you enable them, the chart turns off the `NetworkPolicy` of the Keycloak
Operator. That policy allows port 8080 from all pods. The playbook stops if
the operator does not remove its policy.

| Inventory key | Default | Effect |
|---------------|---------|--------|
| `keycloak.network_policy.enabled` | `false` | Ingress rules. Port 8080 accepts only the Envoy Gateway pods and the Keycloak Operator. Kubelet probes use port 9000. The database accepts Keycloak, its own instances, the CNPG operator, Prometheus and kubelet probes. |
| `keycloak.network_policy.egress` | `false` | Egress rules. Keycloak can reach DNS, the other Keycloak pods, the database and the realm SMTP port. The database can reach DNS, the API server and its own instances. |
| `keycloak.network_policy.cnpg_operator_namespace` | `cnpg-system` | Namespace of the CloudNativePG operator. |
| `keycloak.network_policy.prometheus_namespace` | `""` | Namespace of the Prometheus that scrapes the CNPG metrics port. An empty value removes the rule. |

Enable the ingress rules first and the egress rules second:

1. Set `keycloak.network_policy.enabled: true`.
2. Run `ansible-playbook playbooks/10-keycloak/standup.yml`.
3. Log in to one application through the gateway OIDC flow.
4. Run one `kubectl` command as an OIDC user.
5. Run `kubectl -n keycloak get networkpolicy`.
6. Make sure that `<release_name_prefix>-main-network-policy` is not in the list.
7. Run `hubble observe --namespace keycloak --verdict DROPPED`.
8. Make sure that Hubble shows no dropped flow from a known client.
9. Run `hubble observe --from-namespace keycloak --traffic-direction egress`.
10. Compare the destinations with the egress rules in
    `charts/h3xinfra-keycloak-main/templates/networkpolicy.yaml`.
11. Set `keycloak.network_policy.egress: true`.
12. Run the playbook again.
13. Send a test email from the realm email settings.
14. Do steps 3 to 8 again.

To roll back, set the key to `false` and run the playbook. The Keycloak
Operator then creates its default `NetworkPolicy` again.

## Terraform state

State is kept **locally** under `playbooks/10-keycloak/terraform/` and ignored
by git (see `.gitignore`). For a shared/remote backend, drop a `backend.tf`
next to the other `.tf` files — the `force_init: true` on the Ansible
terraform module will re-init on the next run.

The `terraform.tfvars.json` file is rendered fresh every playbook invocation
and contains plaintext secrets; keep it local only (already git-ignored).


