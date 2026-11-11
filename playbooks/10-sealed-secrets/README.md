# 10-sealed-secrets

Ansible owns the `h3xinfra-sealed-secrets-main` release of the upstream
[`sealed-secrets` chart](https://github.com/bitnami/sealed-secrets/tree/main/helm/sealed-secrets).
The controller decrypts `SealedSecret` objects into Kubernetes Secrets.
Git holds only the encrypted `SealedSecret` objects. Argo CD applies them, and
this controller creates the Secrets.

The layer is opt-in. It is off until you set `sealed_secrets.enabled: true`.

## What the layer installs

- The chart is `sealed-secrets` from `https://bitnami.github.io/sealed-secrets`, at `chart_versions.sealed_secrets`.
  The project moved from `bitnami-labs` to `bitnami` on GitHub. The old URL `https://bitnami-labs.github.io/sealed-secrets` returns 404.
- The controller image is `docker.io/bitnami/sealed-secrets-controller` at `image_versions.sealed_secrets_controller`.
  The value is `<tag>@sha256:<digest>`. The digest is the multi-arch image index. It includes `linux/amd64` and `linux/arm64`.
- The `sealedsecrets.bitnami.com` CRD. The play applies it from the pinned chart with server-side apply (field manager `h3xinfra-sealed-secrets`).
  Helm does not own it. Helm installs `crds/` only once and never upgrades them.
- One Deployment with one replica in the `sealed-secrets` namespace. The namespace enforces Pod Security `restricted`.
- A default-deny ingress NetworkPolicy for the namespace.

Renovate tracks both pins in one group, `Sealed Secrets`. It never automerges them.
The upstream release workflow pushes each tag to `docker.io/bitnami/sealed-secrets-controller` and to
`ghcr.io/bitnami/sealed-secrets-controller` with the same digest. It signs the image with cosign.
The layer uses Docker Hub because Docker Hub returns release timestamps. Thus, the
Renovate minimum release age applies to this image.

### Settings and why

| Setting | Value | Reason |
|---|---|---|
| `keyrenewperiod` | `"0"` | No scheduled key generation. The operator supplies every key. |
| `updateStatus` | `true` | The Argo CD health check reads the SealedSecret `Synced` condition. |
| Key Secret check | before any change | The controller generates a key at startup when it finds none. The play stops first. |
| `fullnameOverride` | `<release_name_prefix>-main` | Repo naming. `kubeseal` defaults to `sealed-secrets-controller` in `kube-system`, but offline sealing does not use the controller. |
| `secretName` (`--key-prefix`) | `sealed_secrets.key_secret_name` | The controller also reads this exact Secret at startup. |
| `priorityClassName` | `h3xinfra-platform-critical` | Every GitOps Secret depends on this controller, like Argo CD. The requests are small. |
| `rbac.serviceProxier` | off | The upstream role lets every authenticated user call the controller through the API server proxy. |
| `networkPolicy.enabled` | `false` | The chart policy admits every source on 8080 and 8081. The release adds default-deny instead. |
| ServiceMonitor | off | No public layer scrapes this controller. To scrape port 8081, add a ServiceMonitor and an ingress rule for Prometheus. |

The container runs as UID/GID 1001 with a read-only root file system, no privilege
escalation, all capabilities dropped and the `RuntimeDefault` seccomp profile. It has
requests and limits for CPU, memory and ephemeral storage.

## Security model

### What the controller can do

The ClusterRole `<release>-unsealer` gives the controller these permissions in all namespaces:

- Secrets: `get`, `list`, `watch`, `create`, `update`, `delete`.
- SealedSecrets: `get`, `list`, `watch`. SealedSecret status: `update`.
- Events: `create`, `patch`.

The Role `<release>-key-admin` in its own namespace gives `get` on the key Secret by name, and `create` and `list` on Secrets.
The render test asserts these rules. A chart update that changes them fails the test.

Thus, the controller can read every Secret in the cluster. Treat its image like
a cluster credential. The image is pinned by digest and signed upstream, and a person reviews each update.

The controller does not overwrite an existing Secret that it does not own. It
writes a Secret only if one of these is true:

- The Secret does not exist yet.
- The Secret has an owner reference to the SealedSecret.
- The Secret has the annotation `sealedsecrets.bitnami.com/managed: "true"`.
- The Secret has the annotation `sealedsecrets.bitnami.com/patch: "true"`. The controller then patches it.

### Trust boundary

Sealing needs only the public certificate. Anybody can seal.

A user who can create or change a SealedSecret in a namespace can make the
controller create a Secret with any value in that namespace. Thus, write access to
SealedSecrets is write access to Secrets in that namespace. With Argo CD, push access
to the GitOps repository gives this access in every namespace that the AppProject allows.

The sealing scope limits where a ciphertext decrypts:

- `strict` (the default): the ciphertext is bound to the namespace and the name. A copy in another namespace, or with another name, does not decrypt.
- `namespace-wide`: the ciphertext is bound to the namespace only.
- `cluster-wide`: the ciphertext is not bound. Anybody who can create a SealedSecret anywhere can move it into a namespace that they control and read the Secret there.

Use `strict`. Use the other scopes only with a written reason.

The private key decrypts every SealedSecret sealed to its certificate. This includes
the full Git history. Whoever holds the key can read all of these values.

### Argo CD

Let Argo CD apply only `bitnami.com/SealedSecret`. Do not allow the core `Secret`
kind in the AppProject. Then a plaintext Secret in Git cannot be deployed.
Argo CD has a built-in health check for SealedSecret. It reports the controller's `Synced` condition.

### No online access

The controller needs no ingress. The default-deny policy and the disabled service
proxier role block the controller endpoints `/v1/cert.pem`, `/v1/verify` and `/v1/rotate`.
Seal offline with the certificate. Thus, `kubeseal --fetch-cert`, `--validate` and `--re-encrypt` do not work.
They are not necessary.

## Configuration

The inventory knobs are in `inventory/production/host_vars/k8s/sealed_secrets.yml`:

| Knob | Default | Meaning |
|---|---|---|
| `sealed_secrets.enabled` | `false` | `stack-standup.yml` and `stack-teardown.yml` skip the layer while it is false. |
| `sealed_secrets.namespace` | `sealed-secrets` | Namespace of the controller and the key. |
| `sealed_secrets.release_name_prefix` | `h3xinfra-sealed-secrets` | The release and the controller are `<prefix>-main`. |
| `sealed_secrets.key_secret_name` | `h3xinfra-sealed-secrets-key` | The key Secret that the play requires. Only lowercase letters, digits and hyphens, at most 245 characters: the controller rejects other `--key-prefix` values at startup. |

A direct run of `standup.yml` deploys the layer independently of `enabled`.
The stack runs it before `10-argocd`. A downstream stack that imports individual
layers must also run it before Argo CD syncs any SealedSecret.

## Provision the sealing key

This layer never creates the key. Provision it separately, before the first run.
An Ansible-managed setup can create the Secret from Ansible Vault in a play that runs before this layer.

1. Generate an RSA-4096 key and a self-signed certificate on a trusted workstation:

   ```bash
   openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
     -keyout sealing.key -out sealing.crt -subj "/CN=sealed-secret/O=sealed-secret"
   chmod 0600 sealing.key
   ```

2. Back up `sealing.key` in two offline places, for example a password manager and encrypted removable media.
   Without the key, no SealedSecret can be decrypted again.
3. Commit `sealing.crt` where the people who seal can find it. The certificate is public.
4. Create the namespace with the Pod Security labels:

   ```bash
   kubectl create namespace sealed-secrets
   kubectl label namespace sealed-secrets \
     pod-security.kubernetes.io/enforce=restricted \
     pod-security.kubernetes.io/audit=restricted \
     pod-security.kubernetes.io/warn=restricted
   ```

5. Create the key Secret with the active label:

   ```bash
   kubectl -n sealed-secrets create secret tls h3xinfra-sealed-secrets-key \
     --cert=sealing.crt --key=sealing.key --dry-run=client -o yaml \
     | kubectl label --local -f - sealedsecrets.bitnami.com/sealed-secrets-key=active -o yaml \
     | kubectl create -f -
   ```

   Use `kubectl create`, not `kubectl apply`. Client-side apply copies the whole Secret, private key included,
   into the annotation `kubectl.kubernetes.io/last-applied-configuration`. Tools that read only metadata can show it.
   `kubectl apply --server-side` does not write that annotation. In Ansible, use `kubernetes.core.k8s` with
   `state: present` and without `apply: true`.
   The play stops if the key Secret has the annotation.

6. Delete the local `sealing.key` after the backup is verified.
7. Set `sealed_secrets.enabled: true`.

The play stops before any change if the Secret is missing, is not
`kubernetes.io/tls`, has no `tls.crt` or `tls.key`, does not have the label
`sealedsecrets.bitnami.com/sealed-secrets-key=active`, or has the annotation
`kubectl.kubernetes.io/last-applied-configuration`.

## Deployment and verification

```bash
ansible-playbook -i /path/to/inventory playbooks/10-sealed-secrets/standup.yml
kubectl -n sealed-secrets get pods,networkpolicies
kubectl -n sealed-secrets logs deploy/h3xinfra-sealed-secrets-main | grep -E 'registered private key|New key written'
```

Do these checks:

1. Make sure that the pod is `Running` and `Ready`.
2. Make sure that the log shows `registered private key` with the name of your key Secret.
3. Make sure that the log does not show `New key written`. If it does, the controller generated a key. Delete that Secret and find out why.
4. Make sure that only your keys have the active label:
   `kubectl -n sealed-secrets get secrets -l sealedsecrets.bitnami.com/sealed-secrets-key`.

## Seal a Secret

Seal offline with the certificate. The controller is not involved.

```bash
kubectl create secret generic app-credentials -n app --from-literal=password=... \
  --dry-run=client -o yaml \
  | kubeseal --cert sealing.crt --scope strict --format yaml > app-credentials.sealedsecret.yaml
```

Do not save the plaintext Secret to a file in the repository.

If you need online access later, give `kubeseal` the controller name and namespace:
`--controller-name h3xinfra-sealed-secrets-main --controller-namespace sealed-secrets`.
This layer blocks online access (see "No online access").

## Rotate the sealing key

The controller decrypts with every key that has the active label. It reads the
keys at startup. Rotation does not change the values of your Secrets.

1. Generate a new key and certificate as in "Provision the sealing key", step 1. Back up the new key.
2. Create the new key Secret with a new name and the active label. Keep the old key Secret.
3. Set `sealed_secrets.key_secret_name` to the new name.
4. Restart the controller: `kubectl -n sealed-secrets rollout restart deploy/h3xinfra-sealed-secrets-main`.
5. Make sure that the log shows `registered private key` for both Secrets.
6. Seal every Secret again with the new certificate. Commit the new SealedSecrets.
7. Make sure that Argo CD reports every SealedSecret as healthy.
8. Delete the old key Secret. Keep its offline backup until no Git revision that you can deploy uses it.
9. Restart the controller again.

The old ciphertexts stay in the Git history. Rotation does not protect them. If a
key leaks, change the secret values themselves (passwords, tokens), then rotate the key.

## Recover a value

You can decrypt a SealedSecret locally with the private key. The controller is not necessary.

```bash
kubeseal --recovery-unseal --recovery-private-key sealing.key --format yaml \
  < app-credentials.sealedsecret.yaml
```

The output is a plaintext Secret. Do not save it to the repository.

To restore the cluster after a loss, do these steps:

1. Create the namespace and the key Secret from the backup ("Provision the sealing key", steps 4 and 5).
2. Run `standup.yml`.
3. Let Argo CD sync. The controller creates the Secrets again.

## Upgrades

A chart or image update arrives in one Renovate pull request (`Sealed Secrets`). Do these checks before you merge it:

1. Read the release notes of the controller and the chart.
2. Make sure that the image tag equals the chart `appVersion`. The test checks this.
3. Run the layer tests. A change of the controller RBAC fails `test_rbac_scope`. Update this README and the test together.
4. Verify the signature with the `cosign.pub` of the release tag:
   `cosign verify --key cosign.pub docker.io/bitnami/sealed-secrets-controller:<tag>@<digest>`.
5. Make sure that the image index includes `linux/amd64` and `linux/arm64`.

The play applies the CRD of the new chart before the Helm upgrade.

## Removal

`teardown.yml` uninstalls the Helm release. It keeps the namespace, the CRD and the key Secrets.

- Existing Secrets stay in place. New or changed SealedSecrets wait until the controller runs again.
- Deleting the CRD deletes every SealedSecret. Kubernetes then deletes every Secret that the controller created, because each one is owned by its SealedSecret.
  Only Secrets sealed with the annotation `sealedsecrets.bitnami.com/skip-set-owner-references: "true"` stay.
- Deleting the key makes every SealedSecret unreadable for a new controller. Restore it from the backup.

This playbook does not delete the CRD, the key or the namespace. Do that separately, and only on purpose.

## Validation

```bash
python3 -m unittest discover -s playbooks/10-sealed-secrets/tests -v
```

The tests download the pinned chart into a temporary directory. For offline runs, set
`SEALED_SECRETS_CHART_DIR` to a chart that you downloaded before. Ansible templates
the real play values, and `helm template` renders them. The tests check these items:

- No key generation (`--key-renew-period 0`) and no Secret in the release.
- The digest-pinned image, and that the image tag equals the chart `appVersion`.
- Pod Security `restricted`, resources and the priority class.
- The default-deny ingress policy as the only NetworkPolicy, and no Ingress or ServiceMonitor.
- The exact controller RBAC, and no service proxier binding.
- The key Secret check runs before any change, fails with a clear message and never prints the key.
- The configuration validation, the CRD check, the teardown scope and the stack guards.
- The Renovate capture of the tag and the digest, and the never-automerge rule.

The tests require Helm and Ansible. They never connect to a cluster.
