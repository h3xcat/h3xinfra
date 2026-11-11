# 10-argocd

Ansible owns the `h3xinfra-argocd-main` release of the upstream
[`argo-cd` chart](https://github.com/argoproj/argo-helm/tree/main/charts/argo-cd).
The chart version is in `chart_versions.argocd`, and Renovate tracks it.
At the start, Argo CD manages no applications. This layer creates no
Application, ApplicationSet or AppProject. Operators add them separately.
Existing Ansible and Terraform resources keep their current owners. Before you
add one of these resources to an Argo CD Application, explicitly move its
ownership to Argo CD.

## Prerequisites and configuration

This layer requires these items:

- K3s
- Cilium
- The `h3xinfra-platform-critical` PriorityClass
- The shared Envoy Gateway with its wildcard certificate and external DNS

Provision native Keycloak OIDC before you enable the layer. Dex is disabled.

Argo CD uses a public OIDC client with PKCE S256 for browser login and for CLI login.
An OIDC client secret is not necessary. The example disables stack deployment
until you set `argocd.enabled: true`. A direct layer deployment validates the
configuration, independently of that stack toggle.

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

The public stack imports this layer conditionally. A downstream stack that
imports individual public layers must add this play after `10-keycloak`.
The public stack does not provision Keycloak. On the first bootstrap, deploy
Keycloak and its client before you opt in to Argo CD.

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
the optional KSOPS age Secret, and the configuration.

Before removal, make a secure backup of this durable state. Also protect the backups of the cluster datastore.
Keep the desired application definitions in Git. Provision repository credentials and the KSOPS age identity separately.

## KSOPS (optional)

KSOPS lets Argo CD build kustomize directories that contain SOPS-encrypted
manifests. It decrypts them with an age identity during the build. KSOPS is
off by default. When it is off, the release renders exactly as without this
feature.

### How it works

When you set `argocd.ksops.enabled: true`, the layer changes only the
repo-server:

- The `install-ksops` init container copies the `ksops` binary from
  `quay.io/viaductoss/ksops` into an `emptyDir` volume. The tag is
  `image_versions.ksops`, and Renovate tracks it.
- The repo-server container mounts that binary read-only at
  `/usr/local/bin/ksops`. Argo CD keeps its bundled kustomize.
- `argocd-cm` gets `kustomize.buildOptions: --enable-alpha-plugins --enable-exec`.
  The bundled kustomize then runs KSOPS as an exec KRM function. These build
  options apply to every kustomize Application.
- The repo-server container mounts the age identity Secret read-only at
  `/app/config/sops-age`. `SOPS_AGE_KEY_FILE` points to
  `/app/config/sops-age/keys.txt`. No other container or pod mounts it.
- `argocd-cmd-params-cm` sets `reposerver.streamed.manifest.max.tar.size`
  and `reposerver.streamed.manifest.max.extracted.size` to `0`. The
  repo-server then rejects streamed local manifests, so
  `argocd app diff --server-side-generate --local` fails. Client-side
  `--local` diffs still work.
- The repo-server pod and its ServiceAccount do not mount a ServiceAccount
  token. The repo-server does not call the Kubernetes API.

The init container passes the `restricted` Pod Security profile. It runs as
UID 65532 with a read-only root filesystem, no capabilities and the
`RuntimeDefault` seccomp profile. It has CPU, memory and ephemeral storage
limits.

| Variable | Default | Meaning |
|---|---|---|
| `argocd.ksops.enabled` | `false` | Install KSOPS and mount the age identity. |
| `argocd.ksops.age_secret_name` | `h3xinfra-argocd-sops-age` | Secret in `argocd.namespace` with the age identity in key `keys.txt`. |
| `image_versions.ksops` | pinned in `versions.yml` | Tag of `quay.io/viaductoss/ksops`. |

### Trust boundary

`--enable-exec` lets a kustomization run any program that is present in the
repo-server container. Thus, the content of a kustomization that Argo CD
builds is equal to code execution in the repo-server. That code runs as the
repo-server user and can do these things:

- Read the age identity and decrypt every file that is encrypted to it.
- Read the repository credentials that Argo CD sends for each build.
- Read the Redis password from the environment. With it, the code can change
  the manifests that the repo-server caches for other Applications. Argo CD
  then deploys those manifests.
- Send data out. Egress from the namespace is not restricted.

Argo CD builds more than the deployed branch. A user with `applications, get`
on an Application can request manifests for any revision of its source
repository. This includes every branch and tag, and any commit that the
repository serves by SHA. On GitHub, this can include commits from forks and
pull requests. Thus, these principals can run code in the repo-server:

- Anyone who can push a branch or tag to an allowed source repository, if
  someone with `applications, get` requests that revision. Bots such as
  Renovate push branches.
- Anyone who has `applications, get` and can open a pull request from a fork
  to an allowed source repository.

The layer rejects streamed local manifests when KSOPS is on. Without that
limit, `applications, get` alone would be enough to run code, because the
user uploads the kustomization.

To limit this risk, do these steps:

1. Use a dedicated age identity for Argo CD. Do not reuse an infrastructure
   or administrator key.
2. Encrypt only the files that Argo CD must decrypt to that identity.
3. Add only trusted repositories. Restrict source repositories and
   destinations with AppProjects. Do not use the built-in `default` project.
4. Restrict pushes, branch creation and tag creation on all refs of each
   allowed source repository to trusted maintainers. For example, use a
   repository ruleset that targets all branches and all tags. Require pull
   request reviews on the deployed branch.
5. Give `applications, get` on KSOPS projects only to trusted roles. Treat
   this permission like a deploy right. Review every role other than
   `role:admin`, for example `role:readonly`, before you enable KSOPS.
6. Do not raise the streamed manifest limits while KSOPS is on.

### Provision the age identity

This layer does not create the age Secret. Provision the age identity
separately, like repository credentials. The standup fails before the Helm
step if the Secret or its `keys.txt` key is missing.

To provision the identity, do these steps:

1. Create a dedicated identity: `age-keygen -o keys.txt`. The command prints
   the public recipient.
2. Store `keys.txt` in your secret store, for example Ansible Vault in your
   inventory. Do not commit it in plain text.
3. Create the Secret in the Argo CD namespace, for example:
   `kubectl -n argocd create secret generic h3xinfra-argocd-sops-age --from-file=keys.txt=keys.txt`.
   Create the namespace first if it does not exist.
4. Delete the local plain-text copy.
5. Set `argocd.ksops.enabled: true` and run the standup again.

In the Git repository, add the recipient to `.sops.yaml`. You can add a second
recipient, so that operators can edit the files with their own key. Refer to
the encrypted files from a KSOPS generator:

```yaml
apiVersion: viaduct.ai/v1
kind: ksops
metadata:
  name: app-secrets
  annotations:
    config.kubernetes.io/function: |
      exec:
        path: ksops
files:
  - ./secret.sops.yaml
```

List the generator under `generators:` in `kustomization.yaml`.

### Rotation

To rotate the age identity, do these steps:

1. Create a new identity, as in "Provision the age identity".
2. Add the new recipient to `.sops.yaml`. Run `sops updatekeys` on each
   encrypted file. Keep the old recipient for now. Merge the change.
3. Update the identity in the secret store that provisions the Secret, for
   example the Ansible Vault variable. Put both the old and the new identity
   in `keys.txt`, one per line, until step 6. Then run the provisioning step
   again. Do not edit the Secret only with `kubectl`: the next provisioning
   run resets it to the stored value.
4. Optional: restart the repo-server:
   `kubectl -n argocd rollout restart deployment/h3xinfra-argocd-main-repo-server`.
   The kubelet updates the mounted file without a restart, because the
   Secret volume does not use `subPath`. A restart applies the change at once.
5. Hard-refresh the Applications. Make sure that they sync.
6. Remove the old recipient from `.sops.yaml`. Run `sops updatekeys` and
   `sops rotate -i` on each file. Merge the change. Then remove the old
   identity from `keys.txt` in the secret store, and run the provisioning step
   again.
7. If the old identity was exposed, also change the secret values that it
   protected.

### Disable KSOPS

Set `argocd.ksops.enabled: false` and run the standup again. Applications
that use KSOPS generators then fail to build, and Argo CD does not sync them.
The streamed manifest limits and the ServiceAccount token return to the chart
defaults.
The layer does not delete the age Secret. Delete it separately.

## Removal and recovery

`teardown.yml` uninstalls the Helm release, including its Argo CD Secret and
HTTPRoute. It keeps the namespace, CRDs and GitOps custom resources. It also
keeps the separately provisioned KSOPS age Secret.
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
chart that you downloaded before. Ansible templates the values, so the play
variables and defaults are the same as in a deployment. Render checks cover HTTPS routing,
PKCE configuration, RBAC, ingress isolation, resources and CRD retention.
KSOPS checks render the layer with KSOPS off and on. With KSOPS off, the
output must be byte-identical to the output without the KSOPS values. With
KSOPS on, the checks cover the init container, the mounts, the environment,
the build options, the streamed manifest limits, the ServiceAccount token and
the `restricted` profile. They also make sure that only the repo-server
container mounts the age Secret.

More tests check the friendly Ansible validation failures, including the
KSOPS Secret check with a mocked lookup result. That test also makes sure
that the Secret data does not stay in a variable after the check. They also check mocked
Terraform plans for public PKCE clients and existing confidential clients. The Terraform
tests copy only the client module definitions into a temporary directory. They
never load deployment state or generated tfvars. The tests require Helm, Ansible and Terraform.
Cluster admission and actual browser/CLI logins still require
deployment checks.
