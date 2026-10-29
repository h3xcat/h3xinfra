# 10-argocd

Ansible owns the `h3xinfra-argocd-main` release of the upstream
[`argo-cd` chart](https://github.com/argoproj/argo-helm/tree/main/charts/argo-cd).
The version lives in `chart_versions.argocd` and is tracked by Renovate.
Argo CD initially manages no applications. Existing Ansible and Terraform
resources keep their current owners; migrate ownership explicitly before
adding them to an Argo CD Application.

## Prerequisites and configuration

Requires K3s, Cilium, the `h3xinfra-platform-critical` PriorityClass, and the
shared Envoy Gateway with its wildcard certificate and external DNS. Native
Keycloak OIDC must be provisioned before enabling the layer. Dex is disabled.

Copy `inventory/production/host_vars/k8s/argocd.yml` into your environment's
inventory. Set the hostname, HTTPS Gateway listener, issuer, client ID and
group-to-role policy. Argo CD uses a public OIDC client with PKCE S256 for both
browser and CLI login; no OIDC client secret is required. The example disables stack deployment
until `argocd.enabled: true` is set. Direct layer deployment validates configuration
regardless of that stack toggle.

Append this entry to your existing `keycloak.clients` list (keep other clients):

```yaml
- name: argocd
  access_type: PUBLIC
  pkce_code_challenge_method: S256
  root_url: "https://argocd.app.{{ domain_name }}"
  redirect_uris:
    - "https://argocd.app.{{ domain_name }}/auth/callback"
    - "https://argocd.app.{{ domain_name }}/pkce/verify"
    - "http://localhost:8085/auth/callback"
  web_origins:
    - "https://argocd.app.{{ domain_name }}"
  groups_claim: true
```

The localhost callback supports `argocd login --sso` on the operator's machine;
`/pkce/verify` supports browser login. Argo CD sets `enablePKCEAuthentication: true`,
and Keycloak enforces S256 code challenges. Existing Keycloak clients still
default to `CONFIDENTIAL` and retain their secrets unless explicitly changed.
The Keycloak Terraform layer creates the public client and an
opt-in group membership mapper. It emits short group names in ID/access tokens
and userinfo, without requiring a realm-wide `groups` client scope. Create the
`argocd-admins` and `argocd-readonly` groups in Keycloak and assign users as
appropriate, or use existing groups in `argocd.rbac.policy_csv`.

The mapper uses `full_path = false`: identically named subgroups under different
parents produce the same claim and match the same RBAC line. Use unique group
names across the realm for these bindings.

The built-in admin and anonymous access are disabled. Authenticated users have
no default access; only explicitly mapped groups receive privileges. The chart
still creates `argocd-secret` for Argo CD's own signing material, but no OIDC
client secret is supplied. Restrict access to namespace Secrets. Provider TLS
verification remains enabled. See the upstream
[Keycloak integration guide](https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/keycloak/).

When migrating an existing confidential Argo CD client, remove its inventory
secret, set the public/S256 options and add `/pkce/verify`. Apply Keycloak before
Argo CD and start a fresh browser session for the new authentication flow.

## Deployment and verification

Run from the public repo with your inventory path (use `h3xinfra/playbooks/...`
when running from the private parent repo):

```bash
ansible-playbook -i /path/to/inventory playbooks/10-keycloak/standup.yml
ansible-playbook -i /path/to/inventory playbooks/10-argocd/standup.yml
kubectl -n argocd get pods,httproutes,networkpolicies
argocd login argocd.app.example.com --sso --grpc-web --skip-test-tls
```

The public stack imports this layer conditionally. A downstream stack that
imports individual public layers must add this play after `10-keycloak`.
The public stack does not provision Keycloak itself; on first bootstrap, deploy
Keycloak and its client before opting in to Argo CD.

Check that the HTTPRoute reports `Accepted` and `ResolvedRefs`, the certificate
matches the hostname, SSO works, administrators can manage applications, and
unmapped users cannot access applications. Use `--grpc-web` for the CLI over
the same HTTPRoute. No separate gRPC endpoint is exposed. The CLI's initial
TLS-detection probe uses native gRPC even with `--grpc-web` and can time out
behind this HTTPRoute. `--skip-test-tls` skips that probe; HTTPS certificate
verification remains enabled. Do not substitute `--insecure` or `--plaintext`.

## Networking and storage

The HTTPRoute attaches only to the selected HTTPS listener. The shared Gateway
handles HTTP redirects and TLS. Its backend uses HTTP on port 8080, confined
by NetworkPolicy to Envoy pods in the configured Gateway namespace. The server
Service is ClusterIP. Native Argo CD authentication applies to UI and API calls;
there is no Gateway OIDC policy. Metrics are not exposed through the HTTPRoute.

The chart's server allow-all NetworkPolicy is disabled; the release supplies
a replacement Gateway-only policy. Other components use upstream ingress
policies, plus namespace-wide default-deny ingress. Egress remains unrestricted
for DNS, Keycloak HTTPS, Git/Helm sources and Kubernetes APIs. Upstream component
policies permit cluster-internal metrics access. Service IPs prefer dual stack.

This is a single-replica installation with bounded CPU, memory and temporary
storage, including init containers and the Redis credential hook. Redis and
repository caches are disposable, so no PVC or Longhorn volume is needed.
Application, ApplicationSet and AppProject objects, repository/cluster Secrets
and configuration in Kubernetes are the durable state. Back these up securely
before removal; protect the cluster's datastore backups as well. Keep desired
application definitions in Git and provision repository credentials separately.

## Removal and recovery

`teardown.yml` uninstalls the Helm release, including its Argo CD Secret and
HTTPRoute, but retains the namespace, CRDs and GitOps custom resources.
Existing managed workloads remain running without reconciliation. This layer
is deliberately absent from automatic stack teardown. Deleting its namespace,
CRDs or Applications separately can cascade to managed workloads through
Application finalizers; those deletions are not part of this playbook.

Restore retained application definitions and separately managed Secrets as
needed, then rerun standup with the same release name, namespace and public
OIDC client. Retained Applications resume reconciliation, including any configured
automated sync/prune. Review their source revisions and policies before restart.

## Validation

```bash
python3 -m unittest discover -s playbooks/10-argocd/tests -v
```

Tests download the pinned upstream chart into a temporary directory and render
the actual playbook values using example hosts and a public client. Set `ARGOCD_CHART_DIR` to an
already-downloaded chart for offline runs. Render checks cover HTTPS routing,
PKCE configuration, RBAC, ingress isolation, resources and CRD retention.
Additional tests exercise friendly Ansible validation failures and mocked
Terraform plans for public PKCE and existing confidential clients. Terraform
tests copy only the client module definitions into a temporary directory and
never load deployment state or generated tfvars. Helm, Ansible and Terraform
are required. Cluster admission and actual browser/CLI logins still require
deployment checks.
