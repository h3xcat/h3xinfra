# 10-keycloak

This layer is the Keycloak identity provider for H3X Infra. The upstream
**Keycloak Operator** (`k8s.keycloak.org/v2beta1`) deploys Keycloak. A
**CloudNativePG** (CNPG) PostgreSQL `Cluster` is the backend. Terraform manages
the realm and client configuration declaratively. Thus, the OIDC clients and
the realm groups stay in sync with the inventory.

## Layout

```
10-keycloak/
├── standup.yml                      # Installs Operator, deploys CRs, runs terraform apply
├── teardown.yml                     # Runs terraform destroy + removes Helm releases
├── charts/
│   ├── h3xinfra-keycloak-pre/        # Bootstrap Secrets, CNPG Cluster, HTTPRoute/SecurityPolicy
│   └── h3xinfra-keycloak-main/       # Keycloak CR, chart-owned PDB, optional Cilium policies
├── terraform/                      # Realm, clients, groups and MFA; backend.tf points to NAS state
├── tasks/cnpg-operator.yml          # Shared CNPG install and guarded ownership handoff
├── cnpg-values.yaml                # Pinned operator resource budgets
└── tests/                          # Mocked contracts and rendered image/HA checks
```

## Deployment flow

### Standup (`ansible-playbook playbooks/10-keycloak/standup.yml`)

First, the standup playbook makes sure that the shared CNPG operator and its
Cluster CRD are ready. The playbook owns the current Helm identity
`cloudnative-pg` in `cnpg-system`. It uses `chart_versions.cloudnative_pg`. It
keeps all three namespace PSA labels at `restricted`. This allows a new
cluster to create the Keycloak database before the private application layers
run. Keycloak and Open WebUI share the operator. The Keycloak teardown keeps it.

A move of the current shared operator needs the coordinated ownership handoff
in [CNPG operator ownership](CNPG-OWNERSHIP.md). A namespace marker makes later
public runs idempotent. But the marker does not prove that the release is no
longer in the private `20-openwebui` Terraform state. The installer refuses
unexpected ownership, values, controller images or adoption schemas. It reuses
the current Helm values. It never forces release ownership. It never replaces
or uninstalls the operator.

After that prerequisite, the standup playbook does these steps:

1. **Keycloak Operator**: The playbook applies the upstream manifests
   pinned to `chart_versions.keycloak`. The Keycloak project publishes the
   Operator as raw manifests, not as a Helm chart. The Deployment of the
   operator must reach `Available` before the reconciliation continues.
2. **Pre-chart** (`h3xinfra-keycloak-pre`) creates these items:
   - A `Secret` (`<prefix>-pre-bootstrap-admin`). It holds the master-realm
     admin username/password. The Keycloak CR uses this Secret through
     `spec.bootstrapAdmin.user.secret`.
   - A **CNPG `Cluster`** (`postgresql.cnpg.io/v1`). It is the backend of Keycloak.
     Its primary and its standby use a required pod anti-affinity. Thus,
     they stay on different nodes. Keycloak connects through the
     `<cluster>-rw` Service that CNPG renders. This Service always points to
     the current primary.
   - One `HTTPRoute` (Gateway API) with named rules. It attaches only to the
     shared Envoy Gateway HTTPS listener. These are the rules:
     - **Public**: `/realms/`, `/resources/`, `/js/`, `/robots.txt` — downstream
       applications and users can reach these paths.
     - **Admin**: `/admin/`, `/metrics`, `/health`, `/realms/master/` — a
       `SecurityPolicy` (`authorization.principal.clientCIDRs`) restricts these paths.
       The values of the policy come from `keycloak.ingress.trusted_setup_ips`.
       The policy targets only the `admin` rule. It uses the rule-level policy
       attachment of Envoy Gateway. An empty allowlist denies admin access.
       The master realm holds the Keycloak admin account. Thus, its sign-in,
       token and account endpoints are in this rule. The longest prefix that
       matches wins. `/realms/master/` uses the admin rule. Every other realm
       uses the public rule. With its default path settings, Envoy Gateway
       normalizes the path before it selects the rule. This covers
       percent-encoded letters and repeated slashes.
       The Ansible controller that runs this playbook must be in the
       allowlist. The readiness checks read `/realms/master/`. Terraform gets
       its token from the master realm before it calls `/admin/`.
3. **Main chart** (`h3xinfra-keycloak-main`) renders a `Keycloak` CR
   (`k8s.keycloak.org/v2beta1`). The Operator reconciles the CR into these items:

   - A StatefulSet.
   - A Service.
   - Soft topology spread constraints across nodes and zones (`ScheduleAnyway`).
   - JGroups KUBE_PING discovery for the Infinispan distributed cache.
   - Rolling updates.

   The spread constraints allow more than one Keycloak pod on one node. Thus,
   the CR also sets a required pod anti-affinity on `kubernetes.io/hostname`.
   Each replica runs on its own node. Thus, `keycloak.replicas` needs the same
   number of schedulable nodes. A replica that has no free node stays
   `Pending`. The other replicas continue to serve requests. The CR prefers
   `amd64` nodes but allows `arm64` nodes. When `instances > 1`, the chart
   renders a `PodDisruptionBudget` with `minAvailable: 1`. The Operator does
   not render one.
   Releases from before chart 0.4.1 wrote the CR as `v2alpha1`. Upgrade them
   with Helm 4.1.1 or later. Helm 4.0 and 4.1.0 also compare the API version
   when they remove the objects of the previous release. Thus, they delete the
   `Keycloak` object right after they update it
   ([helm/helm#31768](https://github.com/helm/helm/issues/31768)).
4. **Readiness probe**: The probe sends requests to `/realms/master/.well-known/openid-configuration`.
   It tries again until it gets `200`. The requests come from the Ansible
   controller that runs the playbook. Thus, the Ansible controller must be in
   `keycloak.ingress.trusted_setup_ips`.
5. **Terraform apply**: The playbook renders `terraform.tfvars.json` from the inventory. It runs
   `terraform init` + `apply` against the new instance. This creates the
   configured realm, the OIDC clients and the groups.

### Teardown (`ansible-playbook playbooks/10-keycloak/teardown.yml -e confirm_destroy=yes-destroy-data`)

The playbook stops at the start unless you give
`-e confirm_destroy=yes-destroy-data`. It deletes the realm, the Helm releases,
the operator, all four Keycloak CRDs and the namespace with its PVCs.
Back up the database and the Terraform state. Then verify the restore before
you continue.
CNPG gives its PVCs a Cluster owner reference. Thus, Kubernetes normally
garbage-collects them after the deletion of the Cluster. The deletion of the
namespace also removes the PVCs that remain. The actual deletion of the
volumes depends on the PV reclaim policy and the storage backend. There is
no name-based PVC sweep or change to retention settings. The shared CNPG
operator and its CRDs are outside this teardown.

The upstream Keycloak manifests contain cluster-wide roles and CRDs. First,
the teardown checks all namespaces. It refuses other custom resources or
bindings that use those roles. The teardown allows only the Helm-owned
Keycloak CR of the main release. The owner of a realm-import, OIDC-client or
SAML-client CR must remove or migrate that CR while the operator still runs.
Unavailable APIs and access errors stop the run. The teardown allows a CRD
that is already absent. These checks need cluster-wide list access to the
four custom resource kinds, to RoleBindings and to ClusterRoleBindings.
In `--check` mode, the ownership checks still run, but Helm only predicts the
deletion. The playbook then skips the wait for the CR cleanup. A check-mode
run cannot prove the cleanup or the completion of the finalizers.

Before a teardown that the owner approved, do these steps:

1. Verify the backups, the state location, the target namespace and the
   release prefix.
2. Make a list of all consumers of the Keycloak operator/CRDs/RBAC. The list
   must also contain empty operator installations.
3. Pause GitOps and other writers for the full maintenance window. The
   preflight cannot prevent a concurrent resource creation.
4. Fix each case of shared ownership. If the preflight fails, do not bypass it.
   Do not remove client finalizers. The operator must finish their cleanup.
5. Run the confirmed teardown in the maintenance window. If Terraform fails,
   the run stops while Keycloak is still available. After Helm removes the
   CRs, the teardown waits until all four kinds are empty. Then it removes the
   operator and the CRDs. Then it removes the namespace. If some resources are
   already absent, you can safely run the teardown again.
6. If the teardown fails, do these steps:
   1. Keep the writers paused.
   2. Check the stage that failed.
   3. If Terraform or the CR cleanup failed, make the operator and the API
      available again.
   4. Fix that cause before you try again.

   After the namespace/storage deletion, a new install of the manifests does
   not restore the data. A restore needs the verified database/state backups.

The ownership and PVC behavior above comes from a source check. The check
used the pinned
[Keycloak 26.8.2 operator manifests](https://github.com/keycloak/keycloak-k8s-resources/blob/26.8.2/kubernetes/kubernetes.yml)
and the [CNPG 1.30.1 PVC ownership code](https://github.com/cloudnative-pg/cloudnative-pg/blob/v1.30.1/api/v1/cluster_funcs.go#L1315-L1320)
([CNPG chart 0.29.1](https://github.com/cloudnative-pg/charts/blob/cloudnative-pg-v0.29.1/charts/cloudnative-pg/Chart.yaml)).
Source checks and synthetic tests do not verify a live teardown or a backup
restore. When you update the operator pin, check the upstream resource set
and these ownership guards again.

## Configuration

The Keycloak settings are in `inventory/production/host_vars/k8s/keycloak.yml`.
The charts need explicit image tags. The playbook gives them
`chart_versions.keycloak` and `image_versions.postgresql` from
`inventory/production/group_vars/all/versions.yml`. The pre-chart always
creates its bootstrap Secrets, its database and its HTTPS route. The
PostgreSQL instances must run on different nodes.

### Required vault-encrypted fields

Use `h3xinfra-gen-pass` to make the vault ciphertext for each field:

| Field                          | Purpose                                     |
|--------------------------------|---------------------------------------------|
| `keycloak.admin.password`      | Master realm admin. Terraform also uses it. |
| `keycloak.db_password`         | Password of the Keycloak application database user (CNPG) |
| `keycloak.realm.smtp.password` | SMTP relay password (if SMTP is enabled)    |
| `keycloak.clients[*].client_secret` | Per-client confidential OIDC secret    |

### Adding an OIDC client

For native Argo CD SSO, use the [Argo CD client example](../10-argocd/README.md).

The default for clients is `access_type: CONFIDENTIAL`. A confidential client
must have a vault-encrypted `client_secret`. Browser/CLI clients can opt into
`access_type: PUBLIC` with `pkce_code_challenge_method: S256`. Public clients
must not have a `client_secret`.

If you set `groups_claim: true` on a client, the layer adds a group membership
mapper. The mapper puts short group names in ID tokens, access tokens and
userinfo. The default value is false. Thus, this setting does not change the
current clients. With short names, subgroups that have the same name under
different parents give the same group name. Thus, use unique names for RBAC
bindings.

`audiences` is a list of other client IDs. For each one, the layer adds an
audience mapper. Thus, the access tokens of this client carry that client in
`aud`. A resource server that checks the audience (for example an MCP server or
an Envoy Gateway JWT policy) can then accept the tokens. ID tokens do not get
the audience.

If you set `optional_scopes`, it is the complete list of optional client
scopes of the client. It replaces the realm defaults. When you add a public
client, review this list. Keep `k8s-cluster` exclusive to the confidential
`k8s-mcp` exchanger. The scope stays optional. A client requests it only
during the exchange.

To allow Open WebUI to call Kubernetes MCP, add these fields to its current client:

```yaml
audiences: ["k8s-mcp"]
optional_scopes: ["address", "phone", "offline_access", "microprofile-jwt"]
```

Set the complete optional-scope list of the exchanger on its current client:

```yaml
optional_scopes: ["address", "phone", "offline_access", "microprofile-jwt", "k8s-cluster"]
```

The exchanger still needs confidential client authentication and
`standard_token_exchange_enabled: true`. The ordinary access token of Open
WebUI carries `k8s-mcp`. The exchanged token carries the `kubernetes` audience
and Kubernetes groups. The public client must not receive `k8s-cluster`.

#### Migrating the existing Open WebUI/MCP resources

The state migration of the owner completed on 2026-11-13. The audience mapper
and both optional-scope assignments keep their original IDs at the generic
`client_audiences["openwebui:k8s-mcp"]`, `clients["openwebui"]` and
`clients["k8s-mcp"]` addresses. The retired Open WebUI self-audience mapper
is gone. A new full Keycloak plan showed no changes and no drift. Thus, the
current source does not contain the three temporary address moves.

The Open WebUI audience mapper keeps its installed display name
`k8s-mcp-audience`. Other audience pairs keep the `audience-<client>` name
pattern. The [mapper update implementation](https://github.com/keycloak/keycloak/blob/26.8.2/model/jpa/src/main/java/org/keycloak/models/jpa/ClientAdapter.java#L460-L474)
of Keycloak 26.8.2 updates the configuration but keeps the name. Thus, a
rename stays in every plan. Do not delete and recreate the mapper to change
its name.

State convergence does not prove a successful browser sign-in or MCP
exchange. Those user-session checks are still open. To restore an older
state, use its [compatible historical source](#terraform-state). That source
also contains the retired moves. Do this before you review the update to the
current source. Other checkouts that share this NAS state are not separate
deployments. The current mocked tests still enforce client authentication,
mapper names and token claims, and complete optional-scope assignments.

Example: a public PKCE client for MCP clients on a workstation (RFC 8252). For
an `http` loopback redirect (`localhost`, `127.0.0.1`, `[::1]`), Keycloak
ignores the port. Thus, one entry accepts every port:

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
members. The default is `[]`. With the default, Terraform manages no extra
groups.

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
| `client_roles` | `{}`    | Client name → role names. Each role must be in the `client_roles` of that client. |
| `members`      | `[]`    | Lowercase usernames of users that exist and are enabled. |

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
removes a member that `members` does not list, for example a user that
someone added in the admin console. `terraform plan` does not show such a member as drift.
Check the *Members* tab of each declared group to see who has its roles.

Terraform does not create users. It looks up each member by exact username.
The plan fails with `user with username <name> not found` when the user does
not exist. It also fails when the user is disabled. On a new realm, Terraform
reads the users only during apply. Thus, add members only after the users
exist.

`keycloak.groups` must not contain `k8s-admins` or `k8s-viewers`.
`terraform/kubernetes_oidc.tf` manages them. A Keycloak admin assigns their
members in the admin console.

A group with the same name can already exist in Keycloak, for example from
an older playbook. Then the apply fails, because Keycloak rejects a second
top-level group with that name. There are two ways to fix it:

- Delete the group in the admin console. Then run standup. Terraform creates
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
   render the file. An older `terraform.tfvars.json` has no `groups`. With
   that file, the import fails with "Configuration for import target does not
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

`tests/test_groups.py` runs all local `*.tftest.hcl` files together with a
mocked provider. `groups.tftest.hcl` covers groups and memberships.
`oidc_clients.tftest.hcl` covers client authentication and claims. The script
copies only the necessary module definitions into a temporary directory. It
does not load deployment state, a backend or generated tfvars.

### Password reset

Keycloak sends the reset link of the *Forgot password* page through the realm
SMTP server (`keycloak.realm.smtp`). The built-in `reset credentials` flow
trusts that e-mail alone. Its *Reset OTP* step also makes the user enroll a
new OTP device. Thus, a person who can read or redirect the realm e-mail can
take an account.

For the application realm, Terraform binds the flow `reset-credentials-otp`
(`terraform/reset_credentials.tf`). The flow has these steps:

1. The user gives the username or the e-mail address.
2. Keycloak sends the reset link to the e-mail address of the user.
3. The user opens the link.
4. If the user has an OTP credential, the user must give a valid OTP code.
5. The user sets a new password.

The flow never removes or replaces an OTP credential. Failed OTP codes count
for the brute-force detection of the realm.

A person who controls the mail path can ask for many reset links and try OTP
codes. From the fifth failure, the brute-force detection locks the user after
each failure. The lock increases by 1 minute each 5 failures, to a maximum of
15 minutes. This limits a person to approximately 140 tries on the first day
and 96 tries on each day after that, for each user.

The realm sends its events to the `jboss-logging` listener
(`terraform/events.tf`). Keycloak adds this listener to each new realm.
Terraform keeps it in the configuration. Keycloak writes each error event to
the pod log at `WARN`. Alloy sends the pod logs to Loki. Loki alert rules in
the private repository read these lines:

- `RESET_PASSWORD_ERROR` with `error="invalid_user_credentials"`: a wrong OTP
  code in the reset flow.
- `error="user_temporarily_disabled"`: a sign-in or reset attempt on a user
  that the brute-force detection locked.

Keycloak gives all events to the listener also when the database stores no
events. Thus, `events_enabled` is `false`. Keycloak logs success events at
`DEBUG`. Thus, the log does not show the lockout event
(`USER_DISABLED_BY_TEMPORARY_LOCKOUT`). Only the next attempt on the locked
user shows the lockout.

CAUTION: Keep `jboss-logging` in `events_listeners`. Without it, the Loki
alerts get no lines. Do not set `spi-events-listener--jboss-logging--*`,
`log`, `log-level` or `log-console-*` on the Keycloak CR. These options can remove
the lines or change their format. `test_realm_events.py` checks the CR
template.

A user without an OTP credential cannot reset the password with this flow.
This also applies to a user with only a WebAuthn credential. After the reset
link, Keycloak shows "Your account has no OTP device. Ask an administrator to reset your password."
The account does not change.

Before you deploy this flow, find the users without an OTP credential. In the
admin console, open *Users* → the user → *Credentials*. A user with OTP has an
`otp` credential. Ask each user without OTP to add an authenticator
application in the account console.

WARNING: Do not send a reset e-mail to a user without OTP. Do not send an
actions e-mail with *Update Password*, *Configure OTP* or *WebAuthn Register*
to a user. These e-mails go through the same mail path. An actions e-mail
does not use the reset flow. A person who controls the mail path can then set
the password and enroll an OTP device, also for a user with OTP.

To reset the password of a user without OTP, do these steps in the admin
console:

1. Verify the identity of the user through a channel other than e-mail, for
   example by phone.
2. Open *Users* → the user → *Credentials*.
3. Click *Reset password*.
4. Set a temporary password. Keep *Temporary* on.
5. Give the temporary password to the user through a channel other than
   e-mail.
6. Open *Details*. Add *Configure OTP* to *Required user actions*.
7. Click *Save*.

At the next sign-in, Keycloak asks the user for a new password and an OTP
device. Use these steps also to give a new user access.

The MFA module (`terraform/modules/mfa-flow`) binds the flow. Its bindings
resource sets all flow bindings of the realm. When the provider creates a
second bindings resource for a realm, it sets the browser flow back to
`browser`. Thus, keep one `keycloak_authentication_bindings` resource for each
realm. The module binds the reset flow only after all steps of the flow exist.
A partial flow can reset a password without the OTP check. Terraform does not
change the reset flow of the master realm.

`reset_credentials.tftest.hcl` checks the steps of the flow with a mocked
provider. `test_reset_credentials.py` checks the binding in `mfa.tf`.
`realm_events.tftest.hcl` checks the events configuration with a mocked
provider.

### Master realm

Terraform manages only the MFA browser flow of the built-in `master` realm
(`mfa.tf`). It does not manage the other realm settings. Thus, it does not
turn on brute-force detection there. Turn it on one time in the admin console:
*master* realm → *Realm settings* → *Security defenses* → *Brute force
detection*. Do not import the realm into Terraform. If you import it,
Terraform manages every master realm setting.

A lockout of the admin user also stops the Terraform sign-in (password grant
on `admin-cli`). The admin route rule keeps the master realm behind
`keycloak.ingress.trusted_setup_ips`. Thus, only allowed sources can cause a
lockout. The password grant cannot send a second factor. Thus, do not require
OTP for the admin user that Terraform uses.

### Proxy headers

The main chart sets the Keycloak `proxy-trusted-addresses` option to the pod
CIDRs (`cluster_cidr.ipv4` and `cluster_cidr.ipv6`). The Envoy pods connect
from these CIDRs. Keycloak ignores `X-Forwarded-*` headers from all other
sources. To trust all sources again, set `keycloak.proxy_trusted_addresses: []`.
All other pods also use these CIDRs. Traffic from the host network can also
use them (the `cilium_host` address). Thus, the main controls are the header
removal in the Gateway and the network policy.

The shared Gateway removes the client copies of these headers before Envoy
sets its own values (`ClientTrafficPolicy` in `h3xinfra-gateway-pre`). To
turn off this policy, set `gateway.client_traffic_policy.enabled: false`.
Then run `playbooks/07-gateway/standup.yml`.

After the deployment, the playbook waits until the StatefulSet replaces all
Keycloak pods. Then it sends `3 * keycloak.replicas` requests to
`/realms/master/.well-known/openid-configuration`. It reads the issuer from
each response. Envoy spreads these requests over the pods. Thus, usually each
pod answers some of them. If Keycloak does not trust the Envoy pods, the
issuer starts with `http://`. Then the playbook stops.

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
| `keycloak.network_policy.cnpg_operator_namespace` | `cnpg-system` | Namespace of the CNPG operator. |
| `keycloak.network_policy.prometheus_namespace` | `""` | Namespace of the Prometheus that scrapes the CNPG metrics port. An empty value removes the rule. |

Enable the ingress rules first and the egress rules second:

1. Set `keycloak.network_policy.enabled: true`.
2. Run `ansible-playbook playbooks/10-keycloak/standup.yml`.
3. Sign in to one application through the Gateway OIDC flow.
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

To roll back, set the key to `false`. Then run the playbook. The Keycloak
Operator then creates its default `NetworkPolicy` again.

## Terraform state

`terraform/backend.tf` configures a `local` backend at
`/h3xinfra/state/keycloak/terraform.tfstate`. This path is on the NAS mount at
`/h3xinfra` on the Ansible controller. Thus, workspace clones share the same
state. The playbook creates the state directory before Terraform runs. The
Ansible Terraform module uses `force_init: true` to initialize the configured
backend on each run.

The MFA and Open WebUI/MCP migrations are complete. The current source does
not need their old address moves. Before you restore an older state, pause the
normal applies. Then use the recorded compatible source revision with the
migrations for that backup. The Git history keeps the pre-cleanup moves. Use
an isolated recovery checkout. Do not replay the historical stack or overwrite
the adopted state only to retire compatibility code. Review the state update
before you start the automation again. Unexpected flow, client or
authentication changes are a stop condition. Keep the recovery copy intact.
The age of the copy is not a reason to keep retired bridges in the normal
deployment configuration.

The playbook renders a new `terraform.tfvars.json` file at each run.

The file contains plaintext secrets. Keep `terraform.tfvars.json` only on the Ansible controller. Git already ignores it.


