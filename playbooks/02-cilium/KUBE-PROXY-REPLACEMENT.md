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

Do not use `127.0.0.1:6444`. The K3s agent load balancer listens there on
agent nodes only. K3s servers that run the API server do not start it.

`playbooks/01-k3s/kube-proxy-preflight.yml` stops `01-k3s/standup.yml` if
the K3s configuration disables kube-proxy and `cilium.k8s_service_host` is
not set.

## Procedure

1. Choose the API server address.
2. Set `k8s_service_host` and `k8s_service_port` (default `6443`) in the
   `cilium` inventory variable.
3. Run `playbooks/02-cilium/standup.yml`. The Cilium agents restart one node
   at a time. Kube-proxy continues to run.
4. Make sure that all Cilium agents are ready:
   `kubectl -n kube-system rollout status ds/cilium`.
5. Make sure that Service traffic and DNS work on all nodes.
6. Start the maintenance window.
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
