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
