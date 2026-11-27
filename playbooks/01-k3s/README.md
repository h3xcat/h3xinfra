# Packaged local storage

Both inventories disable the bundled `local-storage` component of K3s. On a
cluster that already runs, this change uninstalls the local-path provisioner
and its StorageClass. It is an attended change to the storage and the control
plane. Before you deploy `01-k3s`, complete the
[consumer checks, serial rollout and recovery procedure](LOCAL-STORAGE.md).
The `04-health` chart, the PSA labels and the explicit `GET /livez` grant and
check stay in place.

# K3s Install

`standup.yml` runs the roles of the pinned `k3s.orchestration` collection. It
uses the order of `playbooks/site.yml` in the collection:

1. `prereq`, `airgap` and `raspberrypi` on all nodes.
2. `k3s_server` on the control-plane nodes.
3. `k3s_agent` on the agent nodes.

These roles restart K3s on each node in each run.

- The control-plane nodes restart one at a time. If K3s runs on a
  control-plane node, a peer check runs before the role. In this check, the
  API server on each other control-plane node must answer `/readyz`. This is
  the peer check of `tasks/restart-k3s.yml`. After the role, the play waits
  for the local `/readyz`.
- The agent nodes restart one at a time.
- The first failure stops the run before the next node.
- Keep `--forks 1` (`ansible.cfg`) as a second guard.

The join check of the role waits for each new control-plane node. It waits
until all control-plane nodes of the inventory are in the cluster. Thus, a
run with one node at a time cannot create a new HA cluster. It stops at the
second control-plane node. For the first run of a new cluster, pass
`-e k3s_server_serial=0`. This puts all control-plane nodes in one batch, as
`site.yml` does. To add a control-plane node to a cluster that runs, limit
the run to the new node (`--limit <server>`).

The plays copy `site.yml` of collection 1.2.2. After the collection version
changes, compare the plays with the new `site.yml`. Compare the roles, the
order, `become` and `gather_facts`.

The server role writes `/etc/rancher/k3s/config.yaml` with mode 0644. This
file holds the join tokens. `apiserver-config.yml` runs after the install. It
sets the mode to 0600 without a restart. To deploy only this change, run
`apiserver-config.yml`. See
[Apiserver configuration and staged rollout](#apiserver-configuration-and-staged-rollout).

# Recovery: cred/passwd newer than datastore

In this failure, K3s on a control-plane node stops at each start with this
message:

```text
level=fatal msg="/var/lib/rancher/k3s/server/cred/passwd newer than datastore and could cause a cluster outage. Remove the file(s) from disk and restart to be recreated from datastore."
```

K3s keeps a copy of the bootstrap files of the K3s server in the datastore.
These files include the cluster CAs, `cred/passwd` and other files. At start,
the K3s server compares its own files with that copy. It stops if one of its
files is newer and different. `cred/passwd` holds the server and agent
credentials. After a change of `token` or `agent-token`, each K3s server
writes the new credential to its own `cred/passwd` at start. But the
datastore copy keeps the old credential. The start after that fails.

The message tells you to remove `cred/passwd` and restart. Do not use this
step alone. K3s then restores the old copy from the datastore and writes the new
credential again. Then the next restart fails again.

This fix worked one time (K3s v1.37.2):

1. Choose one control-plane node where K3s runs and where K3s started after
   the token change. Do all steps on that node as root.
2. Take an etcd snapshot:
   `k3s etcd-snapshot save --name pre-bootstrap-refresh-$(date -u +%Y%m%dT%H%M%SZ)`.
3. Send the K3s token rotation request one time, with the current password
   of the `server` user as the new token. The token does not change, and K3s
   saves its bootstrap files to the datastore again. These are the details of
   the request:
   - `PUT https://127.0.0.1:6443/v1-k3s/token`, with TLS verification against
     `/var/lib/rancher/k3s/server/tls/server-ca.crt`.
   - HTTP basic authentication: the user `server` and its password. The
     password is the first field of the `server` line in
     `/var/lib/rancher/k3s/server/cred/passwd`.
   - JSON body `{"newToken": "<the same password>"}`.
   - Expect HTTP 200. Do not repeat the request after an error or a timeout.
     Check the result. Keep the password out of the shell history and out of
     command-line arguments.

   The CLI form is `k3s token rotate`, with the current token as the old
   token and as the new token. It sends the same request and is probably
   equivalent. There was no test of this form here.
4. Start K3s on the failed control-plane node.
5. Do the check below.

Do this check after each change of `token` or `agent-token`, and after this
fix:

1. Restart K3s on one control-plane node two times.
2. After each start, look for `newer than datastore` in its K3s journal
   (`journalctl -u k3s --since <time of the restart>`).

The first restart writes `cred/passwd`. The second restart is the one that
fails. Only then restart K3s on the other control-plane nodes, or reboot
them.

# Local Kubelet Watchdog

`kubelet-watchdog.yml` installs a 30-second systemd timer on the cluster nodes.
The timer starts five minutes after boot. The watchdog ignores inactive
services. It also ignores the first 180 seconds after an agent starts.

These responses clear the failure counter:

- A successful local `10248/healthz` response.
- An HTTP 200/401/403 response from `10250/healthz`.

Each of the two probes has a wall-clock timeout of five seconds. On agent
nodes, three consecutive failed checks start recovery. The watchdog always
observes control-plane services and does not recover them. This is also true
if the command line has `--mode recover` by accident.

The watchdog sends SIGQUIT only to the original K3s agent main process. This
signal causes Go to dump goroutines into the service journal. The watchdog
then waits up to 20 seconds. If necessary, it then sends SIGKILL to that same
process. Linux pidfds and repeated systemd invocation checks prevent signals
to a replacement process. K3s must have `Restart=always` and
`KillMode=process`. Then systemd starts the main process again. The watchdog
never restarts the service. It never sends signals to the
containerd/container process group of the service.

Before the watchdog sends a signal, it saves the recovery attempt to its
state file. The limit is two attempts per hour, also across agent restarts or
node reboots. An invalid state or an error from a systemd query blocks
recovery. A drop-in for the journal rate limit of the unit sets a larger
allowance for Go dumps. The installation reloads systemd but does not restart
K3s. The live process can keep the previous journal allowance until its next
start. `systemctl show` verifies the loaded setting. It does not verify that
systemd published the setting to journald. On systemd 255, this result comes
from the
[unit execution preparation](https://github.com/systemd/systemd/blob/v255/src/core/unit.c#L5258-L5416)
and the [journald context refresh](https://github.com/systemd/systemd/blob/v255/src/journal/journald-context.c#L443-L503).
Thus, journald can still rate-limit the first recovery dump.

To deploy only the watchdog, use the usual inventory and vault configuration.
Run these commands from the repository root:

```sh
ansible-playbook playbooks/01-k3s/kubelet-watchdog.yml
python3 -m unittest discover -s playbooks/01-k3s/tests -v
```

For an observation-only rollout, set `kubelet_watchdog_mode: observe` in the
inventory or pass `-e kubelet_watchdog_mode=observe`. Agent nodes use
`recover` by default. Control-plane nodes always use `observe`.

To check the timer, the journal and the state on a node, run these commands:

```sh
systemctl status k3s-kubelet-watchdog.timer
journalctl -u k3s-kubelet-watchdog.service -u k3s-agent.service --since -1h
cat /var/lib/k3s-kubelet-watchdog/state.json
```

The watchdog writes metrics atomically to
`/var/lib/node_exporter/textfile_collector/k3s_kubelet_watchdog.prom`. Node
exporter must mount that directory of the node and enable its textfile
collector. `healthy` is `1` for a passed check, `0` for failed probes, and
`-1` if the watchdog skips the check. Use Prometheus for recovery
notifications and for alerts about blocked recovery.

To disable recovery for maintenance, stop the timer and any in-flight check.
Use this command:
`systemctl stop k3s-kubelet-watchdog.timer k3s-kubelet-watchdog.service`.

The watchdog never starts a stopped K3s service. Keep the state during
maintenance to keep the recovery budget intact. This watchdog does not fence
storage. It also does not prove that API heartbeats and workload
reconciliation advance.

# Hardening Options

These inventory variables control the hardening options of this layer. Each
option is off if the inventory does not set its variable.

| Variable | Effect |
| --- | --- |
| `apiserver_audit.enabled` | On each control-plane node, `apiserver-config.yml` writes an audit policy and an audit log. It restarts K3s on one control-plane node at a time and waits for `/readyz`. |
| `kubelet_protect_kernel_defaults` | The kubelet stops at start if a kernel tunable is different from the kubelet value. `standup.yml` sets and checks the six values first. |
| `kubelet_seccomp_default` | Containers without a seccomp profile get the runtime default profile. |
| `kubelet_allowed_unsafe_sysctls` | A list of exact sysctl names that pods on the node can set in their own namespaces (`spec.securityContext.sysctls`). See [Unsafe Sysctls](#unsafe-sysctls). |
| `secrets-encryption: true` in `server_config_yaml` | K3s encrypts Secrets in the datastore. |

Enable `kubelet_seccomp_default` on one node first. Then recreate the pods on
that node. Make sure that they start. A workload that needs a blocked syscall
fails.

To enable secrets encryption on a cluster that already runs, follow
"Enable Secrets Encryption on an Existing Cluster" in the
[K3s secrets-encrypt documentation](https://docs.k3s.io/cli/secrets-encrypt).
Before you deploy the changed `server_config_yaml`, run
`k3s secrets-encrypt enable` on the first control-plane node. If you do not,
`standup.yml` stops before it restarts the K3s servers. `standup.yml` also
stops if it cannot read the encryption status on the first control-plane
node, for example if K3s does not run.

The audit log stays on each control-plane node. A root user on that node can
change it. To keep a copy that is safe from changes, send the log to a
central log store.

# Apiserver configuration and staged rollout

`apiserver-config.yml` replaces the separate OIDC, admission and audit plays.
It works on one control-plane node at a time. On each node, it reconciles
**all three** feature settings from the selected inventory. Then it does a
maximum of one plain K3s restart and one wait for the local `/readyz`. After
that, it goes to the next node. It does not need a Ready node or a CNI.

Use the complete production inventory, also if you change only OIDC. If the
PSA or audit settings are absent or false, the playbook disables these
features.

The disable behavior stays the same as before:

- If OIDC is disabled, both authentication files stay in place. A change of
  its inventory flag to disabled is not a procedure to remove OIDC.
- If PSA is disabled, the playbook removes its drop-in before its
  AdmissionConfiguration.
- If audit is disabled, the playbook removes only its drop-in. The audit
  policy and the logs stay.

File contents, paths, root ownership, `0600` file modes, `0700` directories
and audit rotation settings are unchanged. Directory-only fixes do not
restart K3s. The collection writes `config.yaml` with mode 0644. The fix to
mode 0600 also does not restart K3s. The audit-policy source stays the same
byte-for-byte, on purpose.

If the inventory has a viewer token, the OIDC smoke test still runs. It runs
after readiness on each control-plane node where the OIDC files changed. The
test expects `get pods` allowed and `create pods` denied. `no_log` hides its
command and its results.

Before you deploy this refactor, save the current configuration and the
current K3s start times. Then do the checks below. In the commands, use your
inventory path and your first control-plane node. From a private checkout,
put `h3xinfra/` before the playbook path.

```bash
ansible-playbook -i /path/to/inventory playbooks/01-k3s/apiserver-config.yml --check --forks 1
ansible-playbook -i /path/to/inventory playbooks/01-k3s/apiserver-config.yml --limit first-server --forks 1
ansible-playbook -i /path/to/inventory playbooks/01-k3s/apiserver-config.yml --forks 1
```

Review each step before you continue. If the live configuration is
unchanged, expect no file changes and no restarts. Investigate each
difference. Do not assume that a render comparison proves the live state.

On the first control-plane node, verify the unchanged start times, the
readiness and the real OIDC access. Do this before you go on to all
control-plane nodes. After the run on all control-plane nodes, verify the
same items on each control-plane node. Do this before you declare the
rollout complete.

`--check` predicts file and service changes. It skips the readiness and
token commands. Thus, it does not prove operational health. These checks
gate the deployment. A merge of the source does not deploy it.

A failed write, restart, readiness check or smoke test stops the serial play.
Earlier writes can leave staged files on the node. Stop. Before you try
again, compare or restore those files. An unchanged staged file does not
cause another restart or OIDC smoke test by itself.

To recover, do these steps:

1. Restore the previous contents and modes of each changed file.
2. Restart only the affected K3s server.
3. Before you go to the next node, verify the readiness and the OIDC access.

For a deliberate rollback of a feature, remove the OIDC drop-in before its
authentication file. For PSA and audit, you can use their disable settings
above. Keep the audit logs during recovery.

# Pod Security Admission

`apiserver-config.yml` sets the cluster-wide Pod Security Admission
defaults. It writes an `AdmissionConfiguration` to
`/etc/rancher/k3s/pod-security-admission.yaml` and a
`config.yaml.d/20-pod-security.yaml` drop-in on each control-plane node. Then
it restarts K3s on one control-plane node at a time and waits for `/readyz`.
The `apiserver_pod_security` inventory variable controls the play. If
`enabled` is `false` or absent, the play removes both files.

The defaults apply only to namespaces that have no
`pod-security.kubernetes.io/*` labels. Namespaces in `exemptions.namespaces`
skip all checks. By default, this list has `kube-system`. Kubernetes also
writes no audit annotations and shows no warnings for pods in an exempt
namespace.

Each layer playbook labels its own namespace. It sets `audit` and `warn` to
`restricted`. It sets `enforce` to the strictest level that the pods of the
namespace pass:

| Namespace variable | `enforce` | Reason |
| --- | --- | --- |
| `cilium.namespace` | `privileged` | hostNetwork, hostPath, host ports, a privileged init container, NET_ADMIN/SYS_ADMIN |
| `metallb.namespace` | `privileged` | L2 speaker: hostNetwork, NET_RAW |
| `certmanager.namespace` | `restricted` | All pods pass `restricted`. |
| `externaldns.namespace` | `restricted` | All pods pass `restricted`. |
| `gateway.namespace` | `restricted` by default | Review the controller, the certgen hook and the generated proxy Pods through the [attended Gateway PSA gate](../07-gateway/README.md#attended-deployment-gate). |
| `longhorn.namespace` | `privileged` | Privileged containers, hostPath `/dev`, `/proc`, `/var/lib/longhorn` |
| `argocd.namespace` | `restricted` | All pods pass `restricted`. |
| `keycloak.namespace` | `privileged` (opt-in `baseline`) | The operators generate the Keycloak and PostgreSQL pods. |
| `kured.namespace` | `privileged` | Privileged container, hostPID |
| `mailu.namespace` | `baseline` | The containers do not set the `restricted` fields. |
| `default`, `kube-public`, `kube-node-lease` | `restricted` | No pods run in these namespaces. `04-health` sets the labels. |

The Cilium, MetalLB, Longhorn and kured namespaces always use the
`privileged` level. To change the `enforce` level of another namespace, set
`<service>.pod_security_enforce` in the inventory. For
`default`, `kube-public` and `kube-node-lease`, use
`health.pod_security_enforce`.

`kubectl debug node/<node>` creates a pod with access to the node in the
current namespace. The `restricted` level in `default` rejects this pod. Add
`-n kube-system` to the command.

The labels and the defaults use the `latest` policy version. A K3s update can
add new Pod Security checks. Then Kubernetes can reject new pods in an
enforced namespace. Before you update K3s, read the Pod Security Standards
changes in the Kubernetes release notes.

The restricted default of the gateway namespace takes effect only after
deployment. Before you change the live namespace, follow its
[attended admission and rollout checks](../07-gateway/README.md). A merge of
the source does not make the enforcement live.

To make an opt-in level active, do these steps:

1. Make sure that you deployed the layer and that its pods run.
2. Run a server-side dry run of the new label:

   ```sh
   kubectl label --dry-run=server --overwrite namespace keycloak \
     pod-security.kubernetes.io/enforce=baseline
   ```

3. If the command shows a warning for a pod, fix the pod before you continue.
4. Set `keycloak.pod_security_enforce: baseline` in the inventory.
5. Run the layer playbook again.

The warnings show only the pods that exist at that time. A controller can
create other pods later, for example during an update.

Do not set `defaults.enforce` higher than `privileged` until each namespace
has its labels. If you do, a namespace without labels gets the stricter
level. Then Kubernetes rejects new privileged pods in that namespace, for
example after a node drain.

# Containerd Registry Configuration

`registries.yml` writes `/etc/rancher/k3s/registries.yaml` on each
control-plane node and each agent node. The file holds registry mirrors and
registry credentials for containerd. `standup.yml` imports `registries.yml`
before the K3s install. Thus, a new node reads the file at its first start.
The schema is in the
[K3s private registry documentation](https://docs.k3s.io/installation/private-registry).

The `k3s_registries` inventory variable (`group_vars/k3s_cluster`) controls the
playbook. If the variable is absent or empty, the playbook changes nothing. A
file that is already on a node stays as it is.

| Key | Effect |
| --- | --- |
| `state` | `present` (default) writes the file. `absent` deletes it. |
| `mirrors` | The `mirrors` section of the file. Each registry takes `endpoint` (a list of URLs) and `rewrite`. |
| `configs` | The `configs` section of the file. Each registry takes `auth` (`username` and `password`, or `auth`, or `identity_token`) and `tls` (`ca_file`, `cert_file`, `key_file`, `insecure_skip_verify`). |

The playbook checks the variable on all nodes before it changes a node. It
stops in these cases:

- A key is unknown.
- `auth` has an empty username or password.
- The inventory also sets `registries_config_yaml`.

K3s ignores unknown keys. Without this check, a typo can remove a credential
without an error.

`registries_config_yaml` is the k3s.orchestration variable for the same file.
With it, the collection writes the file with mode 0644 and does not restart
K3s. Do not use it.

This example gives each node a pull credential for GHCR:

```yaml
k3s_registries:
  configs:
    ghcr.io:
      auth:
        username: example-user
        password: "{{ vault_k3s_registry_ghcr_token }}"
```

A `configs` entry without a `mirrors` entry is enough. K3s v1.37 puts the
credential into the containerd configuration that it generates
(`registry.configs."ghcr.io".auth`). It also writes a `hosts.toml` for
`ghcr.io` with the default endpoint `https://ghcr.io/v2`. Pulls by the kubelet
use the credential. `k3s ctr image pull` does not use it. To test a pull on a
node, use `k3s crictl pull`. Containerd 2.x logs that `configs` is deprecated.
K3s v1.37 still generates it.

## Restarts

K3s reads `registries.yaml` only when it starts. The playbook does these steps:

1. It checks `k3s_registries` on all nodes.
2. It works on the control-plane nodes one at a time. Then it works on the
   agent nodes one at a time.
3. On each node, it writes or deletes the file.
4. It reads the state of the K3s service: `k3s` on control-plane nodes,
   `k3s-agent` on agent nodes. If the service is `inactive`, the playbook
   does not start it. If the service has a state other than `active` or
   `inactive`, the playbook stops. Examples are `failed` and `activating`. In
   this case, fix the problem on the node before you run the playbook again.
5. It restarts K3s only if one of these is true:
   - The file changed in this run.
   - The file is newer than the K3s process that runs.
   - `state` is `absent` and the containerd configuration that K3s generated
     still holds a registry credential.

   The last two cases cover an earlier run that stopped before the restart.
6. Before a K3s server restarts, the API server on each other control-plane
   node must answer `/readyz`. Also, each etcd node must be Ready and not
   cordoned. The K3s API server uses only its local etcd member. Thus,
   `/readyz` proves that the member serves with quorum. Then one restart
   never costs etcd its quorum.
7. Before K3s restarts on a node, kured must not hold its reboot lock. This
   lock is the `weave.works/kured-node-lock` annotation on the kured
   DaemonSet.
8. The restart fails after 600 seconds. The K3s units have no start timeout
   of their own.
9. After a restart, the playbook waits for `/readyz` on a control-plane node.
   On each node, it waits for the kubelet `healthz`. Then it waits for the
   node Ready condition with a heartbeat that is later than the start of the
   new K3s process. An old Ready status from before the restart does not
   count. The node name is the output of `hostname`.
10. The first failure stops the playbook before the next node.

Two shared task files contain the service state check and the guarded
restart (steps 4 and 6 to 10). These files are `tasks/k3s-service-state.yml`
and `tasks/restart-k3s.yml`. `kubelet-sysctls.yml` uses the same files.

The K3s unit uses `KillMode=process`. Thus, the containers continue to run
during the restart. The kubelet watchdog ignores the first 180 seconds after
an agent starts.

To deploy a change without the rest of the layer, run this command from the
repository root:

```sh
ansible-playbook playbooks/01-k3s/registries.yml
```

`--check` shows the nodes that a real run restarts. `--diff` does not show
the file content, because the task that writes the file uses `no_log` and
`diff: false`.

## Credential Rotation

1. Create the new credential. The old credential must stay valid.
2. Replace the vault value, for example:
   `ansible-vault encrypt_string --stdin-name vault_k3s_registry_ghcr_token`.
3. Run `ansible-playbook playbooks/01-k3s/registries.yml`. Every node restarts
   K3s, one node at a time.
4. Pull a private image on one node with `k3s crictl pull`, or recreate a pod
   that uses a private image.
5. Revoke the old credential.

## Removal

1. Set `k3s_registries: {state: absent}`.
2. Run `ansible-playbook playbooks/01-k3s/registries.yml`. It deletes the file
   and restarts K3s on each node, one node at a time.
3. If the run stops early, fix the cause before you run the playbook again.
   A node without the file still restarts K3s if its generated containerd
   configuration holds a registry credential.
4. After the run completes, remove the variable.

If you only remove the variable, the file stays on the nodes.

## Security

- The credential is on the disk of each node. `registries.yaml` belongs to
  root and has mode 0600. K3s also copies the credential into
  `/var/lib/rancher/k3s/agent/etc/containerd/config.toml`. K3s writes that
  file with mode 0644. But its parent directory `/var/lib/rancher/k3s/agent`
  has mode 0700. Only root on the node can read it. Do not loosen the modes
  of these directories.
- A node credential applies to all pods. Any pod on any node can pull each
  image that the credential can read, without an image pull Secret.
  Kubernetes cannot limit a node credential to a namespace.
- An image that a node pulled stays in its image cache. With
  `imagePullPolicy: IfNotPresent`, other pods on that node use it without a
  credential check.
- Give the credential read access only. A GitHub classic token with
  `read:packages` can read every package that its account can read.
- Keep the credential in Ansible Vault. Do not put it in the plain inventory.

# Unsafe Sysctls

A pod can set a sysctl in its own namespaces with
`spec.securityContext.sysctls`. The kubelet allows a small set of safe
sysctls by default. Each other namespaced sysctl must be on the kubelet
allowlist (`allowedUnsafeSysctls`) of the node where the pod runs.

`kubelet-sysctls.yml` writes the allowlist to
`/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/99-unsafe-sysctls.conf`. The
`kubelet_allowed_unsafe_sysctls` inventory variable sets it per host or
group. The default is an empty list. With an empty list, the playbook
removes the file. `standup.yml` writes the same file before the K3s install.
Thus, a new node loads it at its first start.

```yaml
# host_vars of each node where the WireGuard pod can run
kubelet_allowed_unsafe_sysctls:
  - net.ipv4.conf.all.src_valid_mark
```

Rules:

- Each entry is one exact name with dots, for example
  `net.ipv4.conf.all.src_valid_mark`. The playbook refuses patterns, for
  example `net.ipv4.*`. A pattern allows each pod on the node to change
  each sysctl that matches it in its namespace.
- The playbook accepts only the sysctls that the kubelet can allow per pod:
  - `net.*`
  - `fs.mqueue.*`
  - `kernel.sem`
  - `kernel.shmall`
  - `kernel.shmmax`
  - `kernel.shmmni`
  - `kernel.shm_rmid_forced`
  - `kernel.msgmax`
  - `kernel.msgmnb`
  - `kernel.msgmni`

  With any other name, the kubelet does not start. The playbook checks the
  list on all nodes before it changes a node.
- Do not also set the kubelet flag `allowed-unsafe-sysctls`. This flag can
  be in `server_config_yaml`, `agent_config_yaml`, `extra_server_args` or
  `extra_agent_args`. The flag replaces the file. The playbook then fails
  after the restart, because the live kubelet does not have the inventory
  list.
- The kubelet replaces a list from an earlier file. It does not add to that
  list. K3s puts `net.ipv4.ip_forward` and `net.ipv6.conf.all.forwarding`
  into its own `00-k3s-defaults.conf` if its servicelb can run on the node.
  This is always true on agent nodes. This repository disables servicelb.
  Thus, nothing needs these two sysctls. If you use servicelb, add both
  names to the list.

The kubelet reads the file only when K3s starts. The playbook reads the
allowlist of the live kubelet (`/configz`). It restarts K3s only on a node
where this allowlist differs from the inventory. With an empty list, the
playbook compares the allowlist with the K3s defaults. This also covers an
earlier run that stopped before the restart. The restart uses the same
guards as `registries.yml`:

- Control-plane nodes first, then agent nodes.
- One node at a time.
- Peer `/readyz`.
- etcd nodes Ready and not cordoned.
- No kured lock.
- Readiness waits.

After the restart, the playbook checks that the live kubelet has the new
list.

```sh
ansible-playbook playbooks/01-k3s/kubelet-sysctls.yml
```

`--check` shows the nodes that a real run restarts.

## Rollout Order

The kubelet checks the sysctls of a pod after the scheduler places it. If
the allowlist of the node does not have the sysctl, the pod fails with the
reason `SysctlForbidden`. The pod does not start. The scheduler does not try
another node. Thus, use this order:

1. Add the sysctl to `kubelet_allowed_unsafe_sysctls` on each node where the
   pod can run. The `nodeSelector`, the affinity and the tolerations of the
   pod tell where it can run.
2. Run `kubelet-sysctls.yml`.
3. Deploy the workload with the sysctl in `spec.securityContext.sysctls`.
   The namespace must also admit it. Pod Security `baseline` and
   `restricted` reject unsafe sysctls. Thus, the namespace needs
   `privileged`.
4. If the workload needed a node-wide setting before, remove it. For
   `src_valid_mark`, run `00-prep`.

To remove a sysctl, do the steps in reverse. Remove it from the workload
first. Then remove it from the list.

## WireGuard and src_valid_mark

`wg-quick` needs `net.ipv4.conf.all.src_valid_mark=1` for its fwmark
routing. Do not set it on the node. `00-prep` keeps it at `0`. With `1` on
the node, the reverse-path check of the kernel uses the fwmark of Cilium's
transparent DNS proxy (TPROXY, mark `0x200`). It finds the local route of
Cilium's policy routing table for the pod address. Then it drops the
redirected query as a martian source. Each pod with a DNS-aware network
policy (`toFQDNs`) then waits 5 seconds per name, because IPv4 DNS never
answers. IPv6 has no such check.

Set the sysctl in the pod instead (`spec.securityContext.sysctls`). Also add
it to `kubelet_allowed_unsafe_sysctls`. `wg-quick` does not write the sysctl
if its value is already `1`. Thus, the WireGuard container needs no
privileges for it.

# Kata Containers for CI Pods

`kata-ci.yml` installs Kata Containers on a node and adds the containerd
runtime `kata-ci`. A pod with a RuntimeClass for the handler `kata-ci` runs
in its own QEMU virtual machine. The runtime is for CI runner pods. A
privileged container in such a pod, for example `dockerd`, gets root access
only in its virtual machine.

This layer installs only the runtime on the nodes. The RuntimeClass, the
node label and the pods are not in this layer.

These inventory variables control the playbook:

| Variable | Effect |
| --- | --- |
| `kata_ci_enabled` | `true` installs Kata on the node and adds the `kata-ci` runtime. Set it in the `host_vars` of the node. The default is `false`. With `false`, the playbook removes the runtime. |
| `kata_version` | The Kata version, for example `4.4.0`. The playbook needs it on each node with `kata_ci_enabled: true`. |
| `kata_sha256` | The SHA-256 of `kata-static-<kata_version>-amd64.tar.zst`. The playbook needs it on each node with `kata_ci_enabled: true`. |

This example installs Kata 4.4.0 on one node:

```yaml
# group_vars/all: the version and the SHA-256 of its release archive
kata_version: "4.4.0"
kata_sha256: "ccbc0cbc591089d0c57012b78da64b23ef43e0bac0f869dc7bfebe8588c33986"

# host_vars of the node
kata_ci_enabled: true
```

Take `kata_sha256` from the GitHub release metadata of the version. This
command prints it:

```sh
gh release view 4.4.0 --repo kata-containers/kata-containers --json assets \
  --jq '.assets[] | select(.name == "kata-static-4.4.0-amd64.tar.zst") | .digest'
```

The playbook checks both variables on all nodes before it changes a node.
`kata_version` must have the form `<major>.<minor>.<patch>`. `kata_sha256`
must have 64 lowercase hexadecimal characters.

## Installation Steps

On a node with `kata_ci_enabled: true`, the playbook does these steps:

1. It checks that the node is `x86_64` and has `/dev/kvm`. If K3s runs, it
   also checks that containerd has version 2.3 or later. Kata 4.4 needs
   containerd 2.3. K3s v1.37.2 contains containerd 2.3.7.
2. If the release is not on the node, it checks that `/opt` has 10 GiB of
   free space. The archive of Kata 4.4.0 has approximately 1 GiB. The release
   files use approximately 2.1 GiB.
3. It installs the `zstd` package. Then it downloads the release archive from
   GitHub. It verifies the SHA-256 of the archive against `kata_sha256`.
4. It extracts the archive to `/opt/kata-releases/<kata_version>` and deletes
   the archive. The release contains the runtime-rs shim, QEMU, `virtiofsd`,
   the guest kernel and the guest image. The release directory appears only
   after a complete extraction.
5. It writes the hardening drop-in of Kata into the release. See
   [Kata Settings](#kata-settings). The release archive has no `config.d`
   directory, so the playbook creates it first.
6. It points the symlink `/opt/kata` to the release.
7. It loads the kernel modules `vhost_vsock` and `vhost_net`. It also writes
   them to `/etc/modules-load.d`. Thus, the node loads them at each boot.
8. It writes the containerd drop-in
   `/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/50-kata-ci.toml`.
   K3s does not create the directory `config-v3.toml.d`, so the playbook
   creates it first.

K3s v1.37 writes an `imports` line for `config-v3.toml.d/*.toml` into the
containerd configuration that it generates. containerd merges the tables of
an imported file into that configuration. The `runc` runtime stays. The
drop-in sets these values for the `kata-ci` runtime:

| Key | Value | Effect |
| --- | --- | --- |
| `runtime_type` | `io.containerd.kata.v2` | The shim API of Kata. |
| `runtime_path` | `/opt/kata/runtime-rs/bin/containerd-shim-kata-v2` | containerd starts this shim. It does not look for a shim in `PATH`. |
| `privileged_without_host_devices` | `true` | A privileged container gets no devices of the node. |
| `pod_annotations`, `container_annotations` | `[]` | containerd sends no pod annotations and no container annotations to the shim. |
| `options.ConfigPath` | `/opt/kata/share/defaults/kata-containers/runtime-rs/configuration-qemu-runtime-rs.toml` | The shim uses QEMU. |

The drop-in has `version = 3`. containerd reads an imported file without a
version as a version 1 file.

## Kata Settings

The shim reads `configuration-qemu-runtime-rs.toml` at each pod start. It
merges the files of the `config.d` directory next to that file into it. The
shim resolves the symlinks in the path first. Thus, the playbook writes the
drop-in to
`/opt/kata-releases/<kata_version>/share/defaults/kata-containers/runtime-rs/config.d/50-h3xinfra-kata-ci.toml`.
Each release gets its own copy before `/opt/kata` points to it. A change of
this drop-in needs no restart.

| Key | Value | Effect |
| --- | --- | --- |
| `enable_annotations` | `[]` | Pod annotations cannot change the virtual machine. The release default allows `kernel_params`, `default_vcpus` and `default_memory`. |
| `valid_hypervisor_paths`, `valid_virtio_fs_daemon_paths` | `/opt/kata/bin/qemu-system-x86_64`, `/opt/kata/libexec/virtiofsd` | The shim starts only the QEMU and the `virtiofsd` of the release. |
| `seccomp_sandbox` | `on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny` | The seccomp filter of QEMU blocks deprecated system calls (`obsolete`), privilege changes (`elevateprivileges`), new processes (`spawn`) and resource control calls (`resourcecontrol`). |
| `cpu_features` | `pmu=off,vmx=off` | The guest gets no performance counters and no nested virtualization. |
| `debug_console_enabled` | `false` | The guest has no debug console. |
| `emptydir_mode` | `block-plain` | An `emptyDir` volume is a sparse disk image on the node and an ext4 file system in the guest. Thus, `dockerd` can use overlayfs on `/var/lib/docker`. |
| `rootless` | `false` | QEMU runs as root. |

With `rootless = true`, QEMU runs as a temporary user without privileges.
The Kata documentation says that a block device fails if that user cannot
open it. There was no test of `rootless` with privileged containers and
`block-plain` volumes. Thus, `rootless` stays off. The seccomp filter is then
the main limit on a QEMU process that an attacker controls. Test `rootless`
on one node before you change it.

## Restarts and Checks

containerd reads its drop-in only when K3s starts. The playbook reads the
`kata-ci` runtime of the containerd that runs (`k3s crictl info`). It
restarts K3s only if that runtime differs from the runtime that the node must
have. This covers these cases:

- The drop-in is new or changed.
- The playbook removed the drop-in.
- An earlier run stopped before the restart.

The restart uses the shared task files `tasks/k3s-service-state.yml` and
`tasks/restart-k3s.yml`. See [Restarts](#restarts) for their guards. The
playbook works on the control-plane nodes first and then on the agent nodes,
one node at a time. It never starts a stopped K3s.

After the restart, the playbook checks these items:

- The containerd configuration that K3s generated imports
  `config-v3.toml.d/*.toml`. A template `config-v3.toml.tmpl` can remove this
  line.
- The containerd that runs has the `kata-ci` runtime with the values of the
  drop-in. On the other nodes, it has no `kata-ci` runtime.

A new `kata_version` installs a new release and points `/opt/kata` to it.
This change needs no K3s restart. New pods use the new release. Pods that run
keep their release. The playbook does not delete old releases. When no pod
uses an old release, delete its directory by hand.

`standup.yml` imports `kata-ci.yml` before the K3s install, as it imports
`registries.yml`. A new node thus has the drop-in at the first start of K3s.
After the install, a new cluster has no CNI. Then a node cannot become Ready
after a restart. To deploy only this change, run this command from the root
of the repository:

```sh
ansible-playbook playbooks/01-k3s/kata-ci.yml
```

`--check` shows the nodes that a real run restarts. In check mode, the
playbook does not download or extract the release.

Enable Kata on one node first. Then start a test pod with the `kata-ci`
RuntimeClass on that node. Make sure that the pod starts before you enable
Kata on the next node.

## Kata Removal

1. Stop the pods that use the `kata-ci` RuntimeClass on the node. Make sure
   that no new pod with this RuntimeClass can start on the node.
2. Set `kata_ci_enabled: false` for the node, or remove the variable.
3. Run `ansible-playbook playbooks/01-k3s/kata-ci.yml`. It removes the
   containerd drop-in and restarts K3s on the node.

The playbook removes the drop-in before it checks the K3s service. Thus, the
removal also works if the drop-in stops K3s. In that case, the playbook stops
at the service check. systemd starts K3s again without the drop-in. If K3s
shows `failed`, start it with `systemctl restart k3s-agent` (`k3s` on a
control-plane node). Then run the playbook again.

The playbook does not remove `/opt/kata`, `/opt/kata-releases` or the module
files in `/etc/modules-load.d`. Without the containerd drop-in, containerd
does not use them. To remove them, delete them by hand.

## Upstream Facts

The playbook depends on these facts of Kata 4.4.0 and K3s v1.37.2. Check them
again after an update of Kata or K3s:

- The archive `kata-static-<version>-amd64.tar.zst` contains `./opt/kata/`.
  The runtime-rs shim is `runtime-rs/bin/containerd-shim-kata-v2` in it.
- The shim reads `config.d` next to the real path of its configuration file
  (`src/libs/kata-types/src/config/drop_in.rs`).
- The shim takes the configuration path from `options.ConfigPath`
  (`src/runtime-rs/crates/runtimes/common/src/config.rs`).
- K3s imports `config-v3.toml.d/*.toml` from the containerd configuration
  directory (`pkg/agent/templates/templates.go`).
