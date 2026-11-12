# Packaged local storage

Both inventories disable K3s's bundled `local-storage` component. On an existing
cluster this uninstalls the local-path provisioner and its StorageClass; it is
an attended storage/control-plane change. Complete the
[consumer checks, serial rollout and recovery procedure](LOCAL-STORAGE.md)
before deploying `01-k3s`. The `04-health` chart, PSA labels and explicit
`GET /livez` grant/check remain in place.

# Local Kubelet Watchdog

`kubelet-watchdog.yml` installs a 30-second systemd timer on cluster nodes. The
timer starts five minutes after boot. The watchdog ignores inactive services. It
also ignores the first 180 seconds after an agent starts.

These responses clear the failure counter:

- A successful local `10248/healthz` response
- An HTTP 200/401/403 response from `10250/healthz`.

Each of the two probes has a wall-clock timeout of five seconds. On workers,
three consecutive failed checks start recovery. The watchdog always observes
control-plane services without recovery. This is also true when `--mode recover`
is supplied by accident.

The watchdog sends SIGQUIT only to the original K3s agent main process. This signal
causes Go to dump goroutines into the service journal. The watchdog then waits
up to 20 seconds. If necessary, it then sends SIGKILL to that same process. Linux pidfds and
repeated systemd invocation checks prevent signals to a replacement process.
K3s must have `Restart=always` and `KillMode=process`. Then systemd brings the
main process back. The watchdog never restarts the service. It never sends
signals to the containerd/container process group of the service.

The watchdog persists each recovery attempt before it sends a signal. The limit
is two attempts per hour, also across agent or node restarts. Invalid state or
errors from systemd inspection block recovery. A drop-in for the journal rate
limit of the unit sets a larger allowance for Go dumps. The installation reloads
systemd but does not restart K3s. The running process can keep the previous
journal allowance until its next start. `systemctl show` confirms the loaded
setting. It does not confirm the publication of the setting to journald. On
systemd 255, this result comes from its
[unit execution setup](https://github.com/systemd/systemd/blob/v255/src/core/unit.c#L5258-L5416)
and [journald context refresh](https://github.com/systemd/systemd/blob/v255/src/journal/journald-context.c#L443-L503).
Thus, the first recovery dump can still be rate-limited.

To deploy the watchdog independently, use the usual inventory and vault setup.
Run these commands from the repository root:

```sh
ansible-playbook playbooks/01-k3s/kubelet-watchdog.yml
python3 -m unittest discover -s playbooks/01-k3s/tests -v
```

For an observation-only rollout, set `kubelet_watchdog_mode: observe` in
inventory, or pass `-e kubelet_watchdog_mode=observe`. Workers use `recover` by
default. Control-plane nodes are forced to `observe`.

To examine the timer, the journal, and the state on a node, run these commands:

```sh
systemctl status k3s-kubelet-watchdog.timer
journalctl -u k3s-kubelet-watchdog.service -u k3s-agent.service --since -1h
cat /var/lib/k3s-kubelet-watchdog/state.json
```

The watchdog writes metrics atomically to
`/var/lib/node_exporter/textfile_collector/k3s_kubelet_watchdog.prom`. Node
exporter must mount that host directory and enable its textfile collector.
`healthy` is `1` for a passed check, `0` for failed probes, and `-1` when the
check is skipped. Use Prometheus for recovery notifications and blocked-recovery
alerts.

To disable recovery for maintenance, stop both the timer and any in-flight
check. Use this command:
`systemctl stop k3s-kubelet-watchdog.timer k3s-kubelet-watchdog.service`.

The watchdog never starts a stopped K3s service. Keep the state during
maintenance so that the recovery budget stays intact. This watchdog does not fence
storage. It also does not prove that API heartbeats and workload reconciliation
advance.

# Hardening Options

These inventory variables control the hardening options of this layer. Each
option is off when its variable is not set.

| Variable | Effect |
| --- | --- |
| `apiserver_audit.enabled` | `apiserver-audit.yml` writes an audit policy and an audit log on each server. It restarts one server at a time and waits for `/readyz`. |
| `kubelet_protect_kernel_defaults` | The kubelet stops at start if a kernel tunable is not the kubelet value. `standup.yml` sets and checks the six values first. |
| `kubelet_seccomp_default` | Containers without a seccomp profile get the runtime default profile. |
| `kubelet_allowed_unsafe_sysctls` | A list of exact sysctl names that pods on the node may set in their own namespaces (`spec.securityContext.sysctls`). See [Unsafe Sysctls](#unsafe-sysctls). |
| `secrets-encryption: true` in `server_config_yaml` | k3s encrypts Secrets in the datastore. |

Enable `kubelet_seccomp_default` on one node first. Then recreate the pods on
that node and make sure that they start. A workload that needs a blocked
syscall fails.

To enable secrets encryption on a cluster that already runs, follow
"Enable Secrets Encryption on an Existing Cluster" in the
[k3s secrets-encrypt documentation](https://docs.k3s.io/cli/secrets-encrypt).
Run `k3s secrets-encrypt enable` on the first server before you deploy the
changed `server_config_yaml`. If you do not, `standup.yml` stops before it
restarts the servers. `standup.yml` also stops if it cannot read the
encryption status on the first server, for example when k3s does not run.

The audit log stays on each server. A root user on the server can change it.
To keep a copy that is safe from changes, send the log to a central log store.

# Pod Security Admission

`apiserver-admission.yml` sets the cluster-wide Pod Security Admission
defaults. It writes an `AdmissionConfiguration` to
`/etc/rancher/k3s/pod-security-admission.yaml` and a
`config.yaml.d/20-pod-security.yaml` drop-in on each server. Then it restarts
K3s on one server at a time and waits for `/readyz`. The `apiserver_pod_security`
inventory variable controls the play. If `enabled` is `false` or not set, the
play removes both files.

The defaults apply only to namespaces that have no
`pod-security.kubernetes.io/*` labels. Namespaces in
`exemptions.namespaces` (by default `kube-system`) skip all checks. Kubernetes
also writes no audit annotations and shows no warnings for pods in an exempt
namespace. Each layer
playbook labels its own namespace with `audit` and `warn` set to `restricted`
and `enforce` set to the strictest level that its pods pass:

| Namespace variable | `enforce` | Reason |
| --- | --- | --- |
| `cilium.namespace` | `privileged` | hostNetwork, hostPath, host ports, a privileged init container, NET_ADMIN/SYS_ADMIN |
| `metallb.namespace` | `privileged` | hostNetwork, NET_RAW/NET_ADMIN/SYS_ADMIN |
| `certmanager.namespace` | `restricted` | All pods pass `restricted`. |
| `externaldns.namespace` | `restricted` | All pods pass `restricted`. |
| `gateway.namespace` | `privileged` (opt-in `restricted`) | The controller generates the Envoy proxy pods. |
| `longhorn.namespace` | `privileged` | Privileged containers, hostPath `/dev`, `/proc`, `/var/lib/longhorn` |
| `argocd.namespace` | `restricted` | All pods pass `restricted`. |
| `keycloak.namespace` | `privileged` (opt-in `baseline`) | The operators generate the Keycloak and Postgres pods. |
| `kured.namespace` | `privileged` | Privileged container, hostPID |
| `mailu.namespace` | `baseline` | The containers do not set the `restricted` fields. |
| `default`, `kube-public`, `kube-node-lease` | `restricted` | No pods run in these namespaces. `04-health` sets the labels. |

The `privileged` level of the Cilium, MetalLB, Longhorn and kured namespaces
is fixed. For the other namespaces, set `<service>.pod_security_enforce` in the
service inventory to change the `enforce` level.
For `default`, `kube-public` and `kube-node-lease`, use
`health.pod_security_enforce`.

`kubectl debug node/<node>` creates a pod with host access in the current
namespace. The `restricted` level in `default` rejects this pod. Add
`-n kube-system` to the command.

The labels and the defaults use the `latest` policy version. A K3s upgrade
can add new Pod Security checks. Then Kubernetes can reject new pods in an
enforced namespace. Before you upgrade K3s, read the Pod Security Standards
changes in the Kubernetes release notes.

To make an opt-in level active, do these steps:

1. Make sure that the layer is deployed and that its pods run.
2. Run a server-side dry run of the new label:

   ```sh
   kubectl label --dry-run=server --overwrite namespace keycloak \
     pod-security.kubernetes.io/enforce=baseline
   ```

3. If the command shows a warning for a pod, do not continue. Fix the pod first.
4. Set `keycloak.pod_security_enforce: baseline` in the inventory.
5. Run the layer playbook again.

The warnings show only the pods that exist at that time. A controller can
create other pods later, for example during an upgrade.

Do not set `defaults.enforce` higher than `privileged` until each namespace
has its labels. If you do, a namespace without labels gets the stricter
level. Then Kubernetes rejects new privileged pods in that namespace, for
example after a node drain.

# Containerd Registry Configuration

`registries.yml` writes `/etc/rancher/k3s/registries.yaml` on every server and
agent. The file holds registry mirrors and registry credentials for containerd.
`standup.yml` imports `registries.yml` before the K3s install, so a new node
reads the file at its first start. The schema is in the
[K3s private registry documentation](https://docs.k3s.io/installation/private-registry).

The `k3s_registries` inventory variable (`group_vars/k3s_cluster`) controls the
playbook. When the variable is not set or is empty, the playbook changes
nothing. A file that is already on a node stays as it is.

| Key | Effect |
| --- | --- |
| `state` | `present` (default) writes the file. `absent` deletes it. |
| `mirrors` | The `mirrors` section of the file. Each registry takes `endpoint` (a list of URLs) and `rewrite`. |
| `configs` | The `configs` section of the file. Each registry takes `auth` (`username` and `password`, or `auth`, or `identity_token`) and `tls` (`ca_file`, `cert_file`, `key_file`, `insecure_skip_verify`). |

The playbook checks the variable on every node before it changes a node. It
stops when a key is unknown, when `auth` has an empty username or password, or
when `registries_config_yaml` is also set. K3s ignores unknown keys, so a typo
would otherwise remove a credential without an error.
`registries_config_yaml` is the k3s.orchestration variable for the same file.
It writes the file with mode 0644 and does not restart K3s. Do not use it.

This example gives every node a pull credential for GHCR:

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

1. It checks `k3s_registries` on every node.
2. It works on the servers one at a time, then on the agents one at a time.
3. On each node, it writes or deletes the file.
4. It reads the state of the K3s service (`k3s` on servers, `k3s-agent` on
   agents). When the service is `inactive`, the playbook does not start it.
   When the service is in any other state than `active` or `inactive` (for
   example `failed` or `activating`), the playbook stops. Repair the node and
   run the playbook again.
5. It restarts K3s only when one of these is true:
   - The file changed in this run.
   - The file is newer than the running K3s.
   - `state` is `absent` and the containerd configuration that K3s generated
     still holds a registry credential.

   The last two cases cover an earlier run that stopped before the restart.
6. Before a server restarts, the API server on every other server must
   answer `/readyz`, and every etcd node must be Ready and not cordoned. The
   K3s API server uses only its local etcd member, so `/readyz` proves that
   the member serves with quorum. One restart then never costs etcd its
   quorum.
7. Before any node restarts, kured must not hold its reboot lock (the
   `weave.works/kured-node-lock` annotation on the kured DaemonSet).
8. The restart fails after 600 seconds. The K3s units have no start timeout
   of their own.
9. After a restart, a server waits for `/readyz`. Every node waits for the
   kubelet `healthz`. Then it waits for the node Ready condition with a
   heartbeat that is later than the start of the new K3s process. An old
   Ready status from before the restart does not count. The node name is the
   output of `hostname`.
10. The first failure stops the playbook before the next node.

The service state check and the guarded restart (steps 4 and 6 to 10) are
shared task files, `tasks/k3s-service-state.yml` and `tasks/restart-k3s.yml`.
`kubelet-sysctls.yml` uses the same files.

The K3s unit uses `KillMode=process`, so running containers keep running
during the restart. The kubelet watchdog ignores the first 180 seconds after
an agent starts.

To apply a change without the rest of the layer, run this command from the
repository root:

```sh
ansible-playbook playbooks/01-k3s/registries.yml
```

`--check` shows which nodes would restart. `--diff` does not show the file
content, because the task that writes the file uses `no_log` and `diff: false`.

## Credential Rotation

1. Create the new credential. Keep the old one valid.
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
3. When the run stops early, fix the cause and run it again. A node whose
   file is already deleted still restarts when its generated containerd
   configuration holds a registry credential.
4. When the run completes, remove the variable.

If you only remove the variable, the file stays on the nodes.

## Security

- The credential is on the disk of every node. `registries.yaml` is owned by
  root with mode 0600. K3s also copies the credential into
  `/var/lib/rancher/k3s/agent/etc/containerd/config.toml`. K3s writes that
  file with mode 0644, but its parent `/var/lib/rancher/k3s/agent` has mode
  0700. Only root on the node can read it. Do not loosen the modes of these
  directories.
- A node credential applies to every pod. Any pod on any node can pull every
  image that the credential can read, without an image pull Secret.
  Kubernetes cannot limit a node credential to a namespace.
- An image that a node has pulled stays in its image cache. With
  `imagePullPolicy: IfNotPresent`, other pods on that node use it without a
  credential check.
- Give the credential read access only. A GitHub classic token with
  `read:packages` can read every package that its account can read.
- Keep the credential in Ansible Vault. Do not put it in plain inventory.

# Unsafe Sysctls

A pod can set a sysctl in its own namespaces with
`spec.securityContext.sysctls`. The kubelet allows a small set of safe
sysctls by default. Every other namespaced sysctl needs the kubelet
allowlist (`allowedUnsafeSysctls`) of the node where the pod runs.

`kubelet-sysctls.yml` writes the allowlist to
`/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/99-unsafe-sysctls.conf`. The
`kubelet_allowed_unsafe_sysctls` inventory variable sets it per host or
group. The default is an empty list. With an empty list the playbook removes
the file. `standup.yml` writes the same file before the K3s install, so a new
node loads it at its first start.

```yaml
# host_vars of each node where the WireGuard pod can run
kubelet_allowed_unsafe_sysctls:
  - net.ipv4.conf.all.src_valid_mark
```

Rules:

- Each entry is one exact name with dots, for example
  `net.ipv4.conf.all.src_valid_mark`. Patterns such as `net.ipv4.*` are
  refused: a pattern lets every pod on the node change every matching sysctl
  in its namespace.
- Only sysctls that the kubelet can allow per pod are accepted: `net.*`,
  `fs.mqueue.*`, `kernel.sem`, `kernel.shmall`, `kernel.shmmax`,
  `kernel.shmmni`, `kernel.shm_rmid_forced`, `kernel.msgmax`, `kernel.msgmnb`
  and `kernel.msgmni`. With any other name the kubelet does not start. The
  playbook checks the list on every node before it changes a node.
- Do not also set the kubelet flag `allowed-unsafe-sysctls` (in
  `server_config_yaml`, `agent_config_yaml`, `extra_server_args` or
  `extra_agent_args`): it replaces the file. The playbook then fails after the
  restart, because the running kubelet does not have the inventory list.
- The kubelet replaces a list from an earlier file instead of adding to it.
  K3s puts `net.ipv4.ip_forward` and `net.ipv6.conf.all.forwarding` into its
  own `00-k3s-defaults.conf` when its servicelb can run on the node (agents
  always). This repository disables servicelb, so nothing needs them. If you
  use servicelb, add both names to the list.

The kubelet reads the file only when K3s starts. The playbook reads the
allowlist of the running kubelet (`/configz`) and restarts K3s only on a node
where it differs from the inventory (or, with an empty list, from the K3s
defaults). This also covers an earlier run that stopped before the restart.
The restart uses the same guards as `registries.yml` (servers first, then
agents, one node at a time, peer `/readyz`, etcd nodes Ready and not
cordoned, no kured lock, readiness waits). After the restart, the playbook
checks that the running kubelet has the new list.

```sh
ansible-playbook playbooks/01-k3s/kubelet-sysctls.yml
```

`--check` shows which nodes would restart.

## Rollout Order

The kubelet checks the sysctls of a pod after the scheduler has placed it.
On a node without the sysctl on its allowlist, the pod fails with the reason
`SysctlForbidden` and does not start. The scheduler does not try another
node. So use this order:

1. Add the sysctl to `kubelet_allowed_unsafe_sysctls` on every node where the
   pod can run (check its `nodeSelector`, affinity and tolerations). Run
   `kubelet-sysctls.yml`.
2. Deploy the workload with the sysctl in `spec.securityContext.sysctls`.
   The namespace must also admit it: Pod Security `baseline` and `restricted`
   reject unsafe sysctls, so the namespace needs `privileged`.
3. Remove the host-wide setting that the workload needed before, if there
   was one (for `src_valid_mark`, run `00-prep`).

To remove a sysctl, do the steps in reverse: remove it from the workload
first, then from the list.

## WireGuard and src_valid_mark

`wg-quick` needs `net.ipv4.conf.all.src_valid_mark=1` for its fwmark
routing. Do not set it on the host: `00-prep` keeps it at `0`. With `1` on
the host, the reverse-path check of the kernel uses the fwmark of Cilium's
transparent DNS proxy (TPROXY, mark `0x200`). It finds the local route of
Cilium's policy routing table for the pod address and drops the redirected
query as a martian source. Every pod with a DNS-aware network policy
(`toFQDNs`) then waits 5 seconds per name, because IPv4 DNS never answers.
IPv6 has no such check.

Set the sysctl in the pod instead (`spec.securityContext.sysctls`) and add
it to `kubelet_allowed_unsafe_sysctls`. `wg-quick` does not write the sysctl
when its value is already `1`, so the WireGuard container needs no
privileges for it.
