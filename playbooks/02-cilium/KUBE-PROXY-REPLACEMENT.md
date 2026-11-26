# Kube-proxy Replacement

Cilium runs with `kubeProxyReplacement: true`. K3s also runs its embedded
kube-proxy, because `server_config_yaml` sets `disable-kube-proxy: false`. Both
components translate Service addresses on every node. Thus, two components
own the same function.

The repository does not remove kube-proxy by default. A change in either
direction breaks connections that use a Service address at that time. The
[Cilium documentation](https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/)
gives this warning. Use the procedure below in a maintenance window.

## API Server Address

Without `cilium.k8s_service_host`, the Cilium agents and the operator get to
the API server through the `kubernetes` Service (`10.43.0.1:443`). Without
kube-proxy, a Cilium agent that starts cannot translate that address yet.
Thus, Cilium must have an API server address that does not use a Service.

The address must meet these conditions:

- All control-plane nodes and agent nodes can get to it.
- It does not use a Kubernetes Service.
- It continues to work when one control-plane node is not available.
- The API server certificate is valid for it. Add a name to `tls-san` if
  necessary.

K3s servers normally also run the embedded agent. The local client load
balancer of this agent uses port `6444` by default. If the supervisor and the
API server are on the same node, `127.0.0.1:6444` does not depend on an
external DNS or load balancer.
See the [K3s agent options](https://docs.k3s.io/cli/agent#node).
Before you select this address, verify the listener and the API certificate
on every node. Then set `k8s_service_port: 6444` explicitly. A K3s server
without the embedded agent needs another address. A disabled client load
balancer also needs another address. The staged migration below is still
necessary. A change to this guidance does not disable kube-proxy.

`playbooks/01-k3s/kube-proxy-preflight.yml` stops `01-k3s/standup.yml` if
the K3s configuration disables kube-proxy and `cilium.k8s_service_host` has
no value.

The check reads only the inventory. It does not make sure that Cilium in the
cluster uses the address. `playbooks/stack-standup.yml` runs `01-k3s` before
`02-cilium`. Thus, do not set the address and disable kube-proxy in the same
change. Do the procedure steps in sequence.

## Procedure

Use a maintenance window for both the Cilium update in step 3 and the later
K3s migration. First, verify that recovery access is available. The API
address values change the agent PodTemplate directly. The Cilium chart also
starts a DaemonSet rolling update of the agents when the checksum of its
rendered `cilium-config` changes. The adoption of the checksum can restart
them with the host firewall off.
The `maxUnavailable: 1` value of the agent DaemonSet is not a guarantee of
availability for the full fleet. If one or more nodes enforce the host
firewall, plan for the audit-mode reset before the policy and label
reconciliation. See
[agent rollouts and readiness](HOST-FIREWALL.md#agent-rollouts-and-readiness).

CAUTION: Do not do steps 7 to 9 without steps 1 to 5. If you do, the Cilium
agents can fail to start, and the pods on that node lose the network.

1. Choose the API server address.
2. Set `k8s_service_host` and `k8s_service_port` (default `6443`) in the
   `cilium` inventory variable.
3. Run `playbooks/02-cilium/standup.yml`. The API address values change the
   agent PodTemplate and trigger a DaemonSet rolling update. Kube-proxy
   continues to run.
4. Make sure that all Cilium agents are ready:
   `kubectl -n kube-system rollout status ds/cilium`.
5. Make sure that Service traffic and DNS work on all nodes.
6. Before you do the K3s changes in the maintenance window, verify that
   recovery access is still available.
7. Set `disable-kube-proxy: true` in `server_config_yaml`.
8. Run `playbooks/01-k3s/standup.yml`. The K3s servers restart with the new
   configuration.
9. Drain and reboot the nodes, one node at a time. Do this on the
   control-plane nodes and on the agent nodes.

   NOTE: A K3s agent reads `disable-kube-proxy` from the K3s servers when it
   starts. The reboot also removes the kube-proxy iptables rules.
10. On each node, make sure that `iptables-save | grep -c KUBE-SVC` gives `0`.
11. Make sure that Service traffic and DNS work on all nodes.

## Rollback

1. Set `disable-kube-proxy: false` in `server_config_yaml`.
2. Run `playbooks/01-k3s/standup.yml`.
3. Drain and reboot the nodes, one node at a time.

Keep `cilium.k8s_service_host`. It is correct with and without kube-proxy.
