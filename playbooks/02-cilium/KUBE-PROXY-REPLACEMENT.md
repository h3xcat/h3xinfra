# Kube-proxy Replacement

Cilium runs with `kubeProxyReplacement: true`. K3s also runs its embedded
kube-proxy, because `server_config_yaml` sets `disable-kube-proxy: false`. Both
components translate Service addresses on every node. Thus two components
own the same function.

The repository does not remove kube-proxy by default. A change in either
direction breaks connections that use a Service address at that time. The
[Cilium documentation](https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/)
gives this warning. Use the procedure below in a maintenance window.

## API Server Address

Without `cilium.k8s_service_host`, the Cilium agents and the operator get to
the API server through the `kubernetes` Service (`10.43.0.1:443`). Without
kube-proxy, a Cilium agent that starts cannot translate that address yet.
Thus Cilium must have an API server address that does not use a Service.

The address must meet these conditions:

- All nodes can get to it, servers and agents.
- It does not use a Kubernetes Service.
- It continues to work when one K3s server is not available.
- The API server certificate is valid for it. Add a name to `tls-san` if
  necessary.

K3s servers normally also run the embedded agent. Its local client load
balancer defaults to port `6444`; when the supervisor and API server are
colocated, `127.0.0.1:6444` avoids an external DNS or load-balancer dependency.
See the [K3s agent options](https://docs.k3s.io/cli/agent#node).
Before selecting it, verify the listener and API certificate on every node,
and set `k8s_service_port: 6444` explicitly. Agentless servers and a disabled
client load balancer need another address. The staged migration below is
still required; changing this guidance does not disable kube-proxy.

`playbooks/01-k3s/kube-proxy-preflight.yml` stops `01-k3s/standup.yml` if
the K3s configuration disables kube-proxy and `cilium.k8s_service_host` is
not set.

The check reads only the inventory. It does not make sure that Cilium in the
cluster uses the address. `playbooks/stack-standup.yml` runs `01-k3s` before
`02-cilium`. Thus, do not set the address and disable kube-proxy in the same
change. Do the procedure steps in sequence.

## Procedure

Use a maintenance window for both the Cilium update in step 3 and the later
K3s migration. Confirm recovery access first. API address values change the
agent PodTemplate directly. The Cilium chart also rolls agents when its
rendered `cilium-config` checksum changes; checksum adoption can roll them
with the host firewall off.
The agent DaemonSet's `maxUnavailable: 1` is not a fleetwide availability
guarantee. If any nodes enforce the host firewall, account for audit-mode
reset before policy and label reconciliation as described in
[agent rollouts and readiness](HOST-FIREWALL.md#agent-rollouts-and-readiness).

1. Choose the API server address.
2. Set `k8s_service_host` and `k8s_service_port` (default `6443`) in the
   `cilium` inventory variable.
3. Run `playbooks/02-cilium/standup.yml`. The API address values change the
   agent PodTemplate and trigger a DaemonSet rolling update. Kube-proxy
   continues to run.
4. Make sure that all Cilium agents are ready:
   `kubectl -n kube-system rollout status ds/cilium`.
5. Make sure that Service traffic and DNS work on all nodes.
6. Confirm that recovery access remains available before proceeding with the
   K3s changes in the maintenance window.
7. Set `disable-kube-proxy: true` in `server_config_yaml`.
8. Run `playbooks/01-k3s/standup.yml`. The K3s servers restart with the new
   configuration.
9. Drain and reboot the nodes, one node at a time. Do the servers and the
   agents.

   Note: A K3s agent reads `disable-kube-proxy` from the servers when it
   starts. The reboot also removes the kube-proxy iptables rules.
10. On each node, make sure that `iptables-save | grep -c KUBE-SVC` gives `0`.
11. Make sure that Service traffic and DNS work on all nodes.

CAUTION: Do not do steps 7 to 9 without steps 1 to 5. If you do, the Cilium
agents can fail to start, and the pods on that node lose the network.

## Rollback

1. Set `disable-kube-proxy: false` in `server_config_yaml`.
2. Run `playbooks/01-k3s/standup.yml`.
3. Drain and reboot the nodes, one node at a time.

Keep `cilium.k8s_service_host`. It is correct with and without kube-proxy.
