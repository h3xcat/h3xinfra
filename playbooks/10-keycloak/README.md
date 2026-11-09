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

### Teardown (`ansible-playbook playbooks/10-keycloak/teardown.yml -e confirm_destroy=yes-destroy-data`)

The playbook stops at the start unless you give
`-e confirm_destroy=yes-destroy-data`. The teardown deletes the realm, the
CNPG PVCs, and the namespace. You cannot recover this data.

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

   If you do not set `web_origins`, a new client gets no CORS origins. To
   allow the origins of all `redirect_uris`, set `web_origins: ["+"]`.
   Terraform rejects `"*"`.

2. Run `ansible-playbook playbooks/10-keycloak/standup.yml` again. Terraform
   converges only the delta.

Consumers can then use this value:

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

The state is **local**, under `playbooks/10-keycloak/terraform/`. Git ignores
the state (see `.gitignore`). For a shared/remote backend, put a `backend.tf`
next to the other `.tf` files. The Ansible terraform module has `force_init: true`,
so it does the init again on the next run.

The playbook renders a new `terraform.tfvars.json` file at each run.

The file contains plaintext secrets. Keep `terraform.tfvars.json` only on the local machine. Git already ignores it.


