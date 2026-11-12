# 10-keycloak

This layer is the Keycloak identity provider for H3X Infra. The upstream
**Keycloak Operator** (`k8s.keycloak.org/v2alpha1`) deploys Keycloak. A
**CloudNativePG** Postgres `Cluster` is the backend. Terraform manages the realm and client configuration
declaratively. Thus, the OIDC clients and the realm groups stay in sync
with the inventory.

## Layout

```
10-keycloak/
├── standup.yml                      # Installs Operator, deploys CRs, runs terraform apply
├── teardown.yml                     # Runs terraform destroy + removes Helm releases
├── charts/
│   ├── h3xinfra-keycloak-pre/        # Bootstrap Secrets, CNPG Cluster, HTTPRoute/SecurityPolicy
│   └── h3xinfra-keycloak-main/       # Keycloak CR, chart-owned PDB, optional Cilium policies
├── terraform/                      # Realm, clients, groups and MFA; backend.tf points to NAS state
└── tests/                          # Mocked client/group plans and rendered image/HA checks
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
     Its primary and standby use required pod anti-affinity, so
     they stay on different nodes. Keycloak connects through the CNPG-rendered
     `<cluster>-rw` Service. This Service always points to the current primary.
   - One `HTTPRoute` (Gateway API) with named rules. It attaches only to the
     shared Envoy Gateway HTTPS listener. These are the rules:
     - **Public**: `/realms/`, `/resources/`, `/js/`, `/robots.txt` — downstream
       services and end users can reach these paths.
     - **Admin**: `/admin/`, `/metrics`, `/health` — a
       `SecurityPolicy` (`authorization.principal.clientCIDRs`) restricts these paths.
       The values of the policy come from `keycloak.ingress.trusted_setup_ips`. The policy targets only the
       `admin` rule using Envoy Gateway's rule-level policy attachment.
       An empty allowlist denies admin access.
3. **Main chart** (`h3xinfra-keycloak-main`) renders a `Keycloak` CR
   (`k8s.keycloak.org/v2alpha1`). The Operator reconciles the CR into these items:
   a StatefulSet, a Service, soft topology spread constraints across nodes
   and zones (`ScheduleAnyway`), JGroups KUBE_PING discovery for the
   Infinispan distributed cache, and rolling upgrades. The spread constraints
   allow multiple Keycloak pods on one node. When `instances > 1`, the chart
   renders a `PodDisruptionBudget` with `minAvailable: 1`; the Operator does
   not render one.
4. **Readiness probe**: The probe sends requests to `/realms/master/.well-known/openid-configuration`.
   It tries again until it gets `200`.
5. **Terraform apply**: The playbook renders `terraform.tfvars.json` from the inventory. It runs
   `terraform init` + `apply` against the new instance. This creates the
   configured realm, the OIDC clients and the groups.

### Teardown (`ansible-playbook playbooks/10-keycloak/teardown.yml -e confirm_destroy=yes-destroy-data`)

The playbook stops at the start unless you give
`-e confirm_destroy=yes-destroy-data`. It destroys the realm, the Helm releases,
the operator, all four Keycloak CRDs and the namespace, including its PVCs.
Back up the database and Terraform state, and verify recovery before proceeding.
CNPG gives its PVCs a Cluster owner reference, so deleting the Cluster normally
garbage-collects them; namespace deletion also removes remaining PVCs. Actual
volume deletion depends on the PV reclaim policy and storage backend. There is
no name-based PVC sweep or change to retention settings. The shared CNPG
operator and its CRDs are outside this teardown.

The upstream Keycloak manifests include cluster-wide roles and CRDs. Teardown
first checks all namespaces and refuses other custom resources or bindings
that use those roles. Only the main release's Helm-owned Keycloak CR is allowed;
realm-import, OIDC-client and SAML-client CRs must be removed or migrated by their
owner while the operator is still running. Unavailable APIs and access errors stop the run.
An already-absent CRD is allowed. These checks require cluster-wide list access
to the four custom resource kinds and to RoleBindings and ClusterRoleBindings.
In `--check` mode, ownership checks still run, but Helm only predicts deletion;
the subsequent CR cleanup wait is skipped. A check-mode run cannot prove cleanup
or finalizer completion.

Before an owner-approved teardown:

1. Verify backups, state location, target namespace and release prefix. Inventory
   every consumer of the Keycloak operator/CRDs/RBAC, including empty operator
   installations. Pause GitOps and other writers for the entire maintenance
   window; the preflight cannot prevent a concurrent resource creation.
2. Resolve any shared ownership first. Do not bypass a failed preflight or remove
   client finalizers: the operator must finish their cleanup.
3. Run the confirmed teardown in that window. Terraform failure stops the run
   while Keycloak is still available. After Helm removes the CRs, teardown waits
   for all four kinds to be empty before removing the operator and CRDs, then
   removes the namespace. Missing resources remain safe to rerun.
4. On failure, keep writers paused and inspect the failed stage. If Terraform or
   CR cleanup failed, restore operator/API availability and resolve that cause
   before retrying. After namespace/storage deletion, reinstalling manifests
   does not restore data: recovery requires the verified database/state backups.

The ownership and PVC behavior above was checked against the pinned
[Keycloak 26.8.2 operator manifests](https://github.com/keycloak/keycloak-k8s-resources/blob/26.8.2/kubernetes/kubernetes.yml)
and [CNPG 1.30.1 PVC ownership code](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/api/v1/cluster_funcs.go#L1315-L1320)
([CNPG chart 0.29.1](https://github.com/cloudnative-pg/charts/blob/cloudnative-pg-v0.29.1/charts/cloudnative-pg/Chart.yaml)).
Source checks and synthetic tests do not validate a live teardown or backup restore.
Recheck the upstream resource set and these ownership guards when upgrading the operator pin.

## Configuration

Service settings are in `inventory/production/host_vars/k8s/keycloak.yml`.
The charts require explicit image tags: the playbook supplies
`chart_versions.keycloak` and `image_versions.postgresql` from
`inventory/production/group_vars/all/versions.yml`. The pre-chart always
creates its bootstrap Secrets, database and HTTPS route; PostgreSQL instances
must run on different nodes.

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

`audiences` is a list of other client IDs. For each one, the layer adds an
audience mapper, so the access tokens of this client carry that client in
`aud`. A resource server that checks the audience (for example an MCP server or
an Envoy Gateway JWT policy) can then accept the tokens. ID tokens do not get
the audience.

`optional_scopes`, when set, is the complete list of optional client scopes of
the client. It replaces the realm defaults. Review this list when adding a
public client: keep `k8s-cluster` exclusive to the confidential `k8s-mcp`
exchanger. The scope remains optional and is requested only during exchange.

For Open WebUI to call Kubernetes MCP, add these fields to its existing client:

```yaml
audiences: ["k8s-mcp"]
optional_scopes: ["address", "phone", "offline_access", "microprofile-jwt"]
```

Set the exchanger's complete optional-scope list on its existing client:

```yaml
optional_scopes: ["address", "phone", "offline_access", "microprofile-jwt", "k8s-cluster"]
```

The exchanger still needs confidential client authentication and
`standard_token_exchange_enabled: true`. Open WebUI's ordinary access token
carries `k8s-mcp`; the exchanged token carries the `kubernetes` audience and
Kubernetes groups. The public client must not receive `k8s-cluster`.

#### Migrating the existing Open WebUI/MCP resources

Update the client inventory together with this module. Keep `oidc-moved.tf`
until every existing workspace has applied the migration: deleting the old
optional-scope resources without their moves detaches those scope assignments.

Source publication does not perform this migration. Before the first deployment
or apply, the owner must inspect a plan against the real workspace and state,
using the paired module and inventory. Keep normal Keycloak and stack applies
paused until that review is complete. Expect these three address moves:

- `keycloak_openid_audience_protocol_mapper.openwebui_k8s_mcp_audience` to
  `keycloak_openid_audience_protocol_mapper.client_audiences["openwebui:k8s-mcp"]`.
- `keycloak_openid_client_optional_scopes.openwebui` to
  `keycloak_openid_client_optional_scopes.clients["openwebui"]`.
- `keycloak_openid_client_optional_scopes.k8s_mcp` to
  `keycloak_openid_client_optional_scopes.clients["k8s-mcp"]`.

The moved audience mapper also changes its display name from
`k8s-mcp-audience` to `audience-k8s-mcp` in place. The only destruction must be
`keycloak_openid_audience_protocol_mapper.openwebui_self_audience`, which added
`openwebui` to access tokens. MCP checks for `k8s-mcp`; this mapper did not add
anything to ID tokens. There must be no creation, replacement, client or scope
detachment, or authentication change. Resolve any unexpected action before apply.

After applying, verify Open WebUI login and its MCP exchange, then remove the
temporary moves only after all existing workspaces have recorded the new addresses.
The mocked migration checks establish source behavior; they do not replace the
owner's live plan or runtime checks.

Example: a public PKCE client for MCP clients on a workstation (RFC 8252). For
an `http` loopback redirect (`localhost`, `127.0.0.1`, `[::1]`), Keycloak
ignores the port, so one entry accepts every port:

```yaml
- name: "mcp-clients"
  access_type: "PUBLIC"
  pkce_code_challenge_method: "S256"
  redirect_uris:
  - "http://localhost/callback"
  - "http://127.0.0.1/callback"
  groups_claim: true
  audiences: ["k8s-mcp"]
  optional_scopes: ["offline_access"]
```

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

### Groups and memberships

`keycloak.groups` declares realm groups. Each group can get client roles and
members. The default is `[]`, and then Terraform manages no extra groups.

```yaml
keycloak:
  clients:
  - name: "reports-cms"
    client_secret: !vault |
      ...
    redirect_uris:
    - "https://reports.app.example.com/oauth2/callback"
    client_roles: ["report-editor", "report-publisher"]
  groups:
  - name: "report-editors"
    client_roles:
      reports-cms: ["report-editor"]
  - name: "report-publishers"
    client_roles:
      reports-cms: ["report-publisher"]
    members: ["alice"]
```

These are the fields:

| Field          | Default | Purpose |
|----------------|---------|---------|
| `name`         | —       | Top-level group name. It must be unique and must not contain `/`. |
| `client_roles` | `{}`    | Client name → role names. Each role must be in that client's `client_roles`. |
| `members`      | `[]`    | Lowercase usernames of existing, enabled users. |

The role mappings are authoritative. Terraform sets the role mappings of a
declared group to exactly its `client_roles` (`exhaustive = true`). It removes
a role that someone added in the admin console. A group with no
`client_roles` has no role mappings. Thus, the inventory is the complete
role grant of each declared group.

The memberships are additive. Terraform adds each member to the group
(`keycloak_user_groups` with `exhaustive = false`). It does not change the
other groups of the user, for example `k8s-admins`. When you remove a user
from `members`, Terraform removes only that membership. When you remove a
group, Terraform deletes the group, and all of its members lose it.

Thus, `members` is a minimum, not the full member list. Terraform never
removes a member that `members` does not list, for example a user added in
the admin console. `terraform plan` does not show such a member as drift.
Check the *Members* tab of each declared group to see who has its roles.

Terraform does not create users. It looks up each member by exact username.
The plan fails with `user with username <name> not found` when the user does
not exist. It also fails when the user is disabled. On a new realm, Terraform
reads the users only during apply. Thus, add members only after the users
exist.

`k8s-admins` and `k8s-viewers` are not allowed in `keycloak.groups`.
`terraform/kubernetes_oidc.tf` manages them, and an admin assigns their
members in the admin console.

A group with the same name can already exist in Keycloak, for example from
an older playbook. Then the apply fails, because Keycloak rejects a second
top-level group with that name. There are two ways to fix it:

- Delete the group in the admin console, then run standup. Terraform creates
  the group again with only the declared roles and members. The old members
  lose the group roles until the run ends.
- Import the group. This keeps the group id and its members.

To import a group, do these steps:

1. Add the group to `keycloak.groups` in the inventory.
2. Render `terraform.tfvars.json` without an apply:

   ```sh
   ansible-playbook playbooks/10-keycloak/standup.yml --tags keycloak-tfvars
   ```

   The tag runs only the tasks that create the Terraform directories and
   render the file. An older `terraform.tfvars.json` has no `groups`, and
   then the import fails with "Configuration for import target does not
   exist".
3. Find the group id in the admin console (*Groups* → the group → the id in
   the URL).
4. Import the group. Run these commands in `playbooks/10-keycloak/terraform`:

   ```sh
   terraform init
   terraform import -var-file=terraform.tfvars.json \
     'keycloak_group.groups["report-editors"]' '<realm>/<group-id>'
   ```

5. Open the *Members* tab of the group. Remove every user that `members` does
   not list. Terraform does not remove them.
6. Run `ansible-playbook playbooks/10-keycloak/standup.yml`. Terraform then
   sets the role mappings and adds the members.

`tests/test_groups.py` runs all local `*.tftest.hcl` files together with a mocked
provider: `groups.tftest.hcl` covers groups and memberships, and
`oidc_clients.tftest.hcl` covers client authentication and claims. It copies only
the required module definitions into a temporary directory; no deployment state,
backend or generated tfvars are loaded.

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

`terraform/backend.tf` configures a `local` backend at
`/h3xinfra/state/keycloak/terraform.tfstate`. This path is on the NAS mounted
at `/h3xinfra` in the controller, so workspace clones share the same state.
The playbook creates the state directory before Terraform runs. The Ansible
Terraform module uses `force_init: true` to initialize the configured backend
on each run.

The playbook renders a new `terraform.tfvars.json` file at each run.

The file contains plaintext secrets. Keep `terraform.tfvars.json` only on the local machine. Git already ignores it.


