# 10-argocd

Ansible owns the `h3xinfra-argocd-main` release of the upstream
[`argo-cd` chart](https://github.com/argoproj/argo-helm/tree/main/charts/argo-cd).
The chart version is in `chart_versions.argocd`, and Renovate tracks it.
At the start, Argo CD manages no applications. Existing Ansible and Terraform
resources keep their current owners. Before you add one of these resources to an
Argo CD Application, explicitly move its ownership to Argo CD.

## Prerequisites and configuration

This layer requires these items:

- K3s
- Cilium
- The `h3xinfra-platform-critical` PriorityClass
- The shared Envoy Gateway with its wildcard certificate and external DNS

Provision native Keycloak OIDC before you enable the layer. Dex is disabled.

Argo CD uses a public OIDC client with PKCE S256 for browser login and for CLI login.
An OIDC client secret is not necessary. Deploy this layer directly after
provisioning Keycloak. The layer validates its configuration before deployment.

To configure the layer, do these steps:

1. Copy `inventory/production/host_vars/k8s/argocd.yml` into the inventory of your environment.
2. Set the hostname, the HTTPS Gateway listener, the issuer, and the client ID.
3. Set the group-to-role policy.
4. Append this entry to your existing `keycloak.clients` list. Keep the other clients.

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

The localhost callback supports `argocd login --sso` on the machine of the operator.
The `/pkce/verify` callback supports browser login. Argo CD sets `enablePKCEAuthentication: true`,
and Keycloak enforces S256 code challenges. Existing Keycloak clients
continue to use `CONFIDENTIAL` as the default. They keep their secrets if you do not explicitly change them.

The Keycloak Terraform layer creates the public client and an
opt-in group membership mapper. The mapper puts short group names in ID/access tokens
and in userinfo. It does not require a realm-wide `groups` client scope. Do one of these two steps:

- Create the `argocd-admins` and `argocd-readonly` groups in Keycloak, and assign the applicable users to them.
- Use existing groups in `argocd.rbac.policy_csv`.

The mapper uses `full_path = false`. Thus, subgroups that have the same name under different
parents give the same claim and match the same RBAC line. Use unique group
names across the realm for these bindings.

The built-in admin and anonymous access are disabled. Authenticated users have
no default access. Only explicitly mapped groups get privileges. The chart
still creates `argocd-secret` for the signing material of Argo CD, but the release supplies no OIDC
client secret. Restrict access to namespace Secrets. Provider TLS
verification stays enabled. Refer to the upstream
[Keycloak integration guide](https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/keycloak/).

To migrate an existing confidential Argo CD client, do these steps:

1. Remove its inventory secret.
2. Set the public/S256 options.
3. Add `/pkce/verify`.
4. Apply Keycloak before Argo CD.
5. Start a new browser session for the new authentication flow.

## Deployment and verification

Run these commands from the public repo with your inventory path. If you run them
from the private parent repo, use `h3xinfra/playbooks/...`.

```bash
ansible-playbook -i /path/to/inventory playbooks/10-keycloak/standup.yml
ansible-playbook -i /path/to/inventory playbooks/10-argocd/standup.yml
kubectl -n argocd get pods,httproutes,networkpolicies
argocd login argocd.app.example.com --sso --grpc-web --skip-test-tls
```

The public stack and `h3xinfra-deploy-stack` do not deploy Argo CD or Keycloak.
Previously, `argocd.enabled: true` added Argo CD to the public stack; that toggle
has been removed. Use the direct layer commands above, or add this play after
`10-keycloak` in a downstream stack.

Do these checks:

1. Make sure that the HTTPRoute reports `Accepted` and `ResolvedRefs`.
2. Make sure that the certificate matches the hostname.
3. Make sure that SSO works.
4. Make sure that administrators can manage applications.
5. Make sure that unmapped users cannot access applications.

Use `--grpc-web` for the CLI over the same HTTPRoute. There is no separate gRPC endpoint. The initial
TLS-detection probe of the CLI uses native gRPC, also when you use `--grpc-web`. Thus, the probe can time out
behind this HTTPRoute. The `--skip-test-tls` flag skips that probe. HTTPS certificate
verification stays enabled.

Do not use `--insecure` or `--plaintext` as a replacement.

## Networking and storage

The HTTPRoute attaches only to the selected HTTPS listener. The shared Gateway
does the HTTP redirects and the TLS. The backend uses HTTP on port 8080. A NetworkPolicy
allows access to the backend only from Envoy pods in the configured Gateway namespace. The server
Service is ClusterIP. Native Argo CD authentication applies to UI and API calls.
There is no Gateway OIDC policy. The HTTPRoute does not expose metrics.

The chart has an allow-all NetworkPolicy for the server. This policy is disabled. The release supplies
a replacement policy that allows only the Gateway. Other components use upstream ingress
policies and a namespace-wide default-deny ingress policy. Egress stays unrestricted
for DNS, Keycloak HTTPS, Git/Helm sources and Kubernetes APIs. Upstream component
policies permit cluster-internal metrics access. Service IPs prefer dual stack.

This installation has a single replica with limits for CPU, memory and temporary
storage. The limits include init containers and the Redis credential hook. The Redis and
repository caches are disposable, so no PVC or Longhorn volume is necessary.
The durable state is in Kubernetes. It includes the Application, ApplicationSet and AppProject objects, the repository/cluster Secrets,
and the configuration.

Before removal, make a secure backup of this durable state. Also protect the backups of the cluster datastore.
Keep the desired application definitions in Git. Provision repository credentials separately.

## Removal and recovery

`teardown.yml` uninstalls the Helm release, including its Argo CD Secret and
HTTPRoute. It keeps the namespace, CRDs and GitOps custom resources.
Existing managed workloads continue to run without reconciliation. The automatic stack teardown
does not include this layer. This is intentional.

You can delete the namespace, the CRDs or the Applications of this layer separately.
If you do, Application finalizers can cascade these deletions to the managed workloads.
This playbook does not do these deletions.

To recover the layer, do these steps:

1. Restore the retained application definitions and the separately managed Secrets, as necessary.
2. Review the source revisions and policies of the retained Applications.
3. Run standup again with the same release name, namespace and public OIDC client.

The retained Applications start reconciliation again. This includes any configured
automated sync/prune. Thus, do step 2 before you restart.

## Validation

```bash
python3 -m unittest discover -s playbooks/10-argocd/tests -v
```

The tests download the pinned upstream chart into a temporary directory. They render
the actual playbook values with example hosts and a public client. For offline runs, set `ARGOCD_CHART_DIR` to a
chart that you downloaded before. Render checks cover HTTPS routing,
PKCE configuration, RBAC, ingress isolation, resources and CRD retention.

More tests check the friendly Ansible validation failures. They also check mocked
Terraform plans for public PKCE clients and existing confidential clients. The Terraform
tests copy only the client module definitions into a temporary directory. They
never load deployment state or generated tfvars. The tests require Helm, Ansible and Terraform.
Cluster admission and actual browser/CLI logins still require
deployment checks.
