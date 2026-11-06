# Local Kubelet Watchdog

`kubelet-watchdog.yml` installs a 30-second systemd timer on cluster nodes. It
starts five minutes after boot and ignores inactive services or the first
180 seconds after an agent starts. A successful local `10248/healthz` response
or an HTTP 200/401/403 response from `10250/healthz` clears the failure counter.
Both probes have a five-second wall-clock timeout. Three consecutive failed
checks trigger recovery on workers; control-plane services are always observed
without recovery, including when `--mode recover` is accidentally supplied.

The watchdog sends SIGQUIT only to the original K3s agent main process so Go
dumps goroutines into the service journal. It waits up to 20 seconds before
sending SIGKILL to that same process if needed. Linux pidfds and repeated
systemd invocation checks prevent signaling a replacement. K3s must have
`Restart=always` and `KillMode=process`; systemd brings the main process back.
The watchdog never restarts the service or signals its containerd/container
process group.

Recovery attempts are persisted before signaling and limited to two per hour,
including across agent or node restarts. Invalid state or systemd inspection
errors block recovery. A unit journal rate-limit drop-in configures a larger
allowance for Go dumps. Installation reloads systemd without restarting K3s;
the running process may retain the previous journal allowance until its next
start. `systemctl show` confirms the loaded setting, not its publication to
journald. On systemd 255, this follows from its
[unit execution setup](https://github.com/systemd/systemd/blob/v255/src/core/unit.c#L5258-L5416)
and [journald context refresh](https://github.com/systemd/systemd/blob/v255/src/journal/journald-context.c#L443-L503).
The first recovery dump can therefore still be rate-limited.

Deploy independently from the repository root, using the usual inventory and
vault setup:

```sh
ansible-playbook playbooks/01-k3s/kubelet-watchdog.yml
python3 -m unittest discover -s playbooks/01-k3s/tests -v
```

Set `kubelet_watchdog_mode: observe` in inventory or pass
`-e kubelet_watchdog_mode=observe` for an observation-only rollout. Workers
default to `recover`; control-plane nodes are forced to `observe`.

On a node, inspect the timer, journal and state with:

```sh
systemctl status k3s-kubelet-watchdog.timer
journalctl -u k3s-kubelet-watchdog.service -u k3s-agent.service --since -1h
cat /var/lib/k3s-kubelet-watchdog/state.json
```

Metrics are atomically written to
`/var/lib/node_exporter/textfile_collector/k3s_kubelet_watchdog.prom`; node
exporter must mount that host directory and enable its textfile collector.
`healthy` is `1` for a passed check, `0` for failed probes, and `-1` when skipped.
Recovery notifications and blocked-recovery alerts belong in Prometheus.
Disable recovery for maintenance by stopping both the timer and any in-flight
check with `systemctl stop k3s-kubelet-watchdog.timer k3s-kubelet-watchdog.service`.
A stopped K3s service is never started by the watchdog. Retain state during
maintenance so the recovery budget remains intact. This watchdog does not
fence storage or prove that API heartbeats and workload reconciliation are
advancing.

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
