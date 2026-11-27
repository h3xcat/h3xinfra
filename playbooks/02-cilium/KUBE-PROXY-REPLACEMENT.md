# Kube-proxy Replacement

Cilium runs with `kubeProxyReplacement: true`. K3s also runs its embedded
kube-proxy, because `server_config_yaml` sets `disable-kube-proxy: false`. Both
components translate Service addresses on every node. Thus, two components
own the same function.

Cilium limits its socket LB to the host network namespace. Pods get the
per-packet LB of Cilium. See [Socket LB and Pods](#socket-lb-and-pods).

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

## Socket LB and Pods

Cilium translates a Service address in one of two places:

- Socket LB: BPF programs on the cgroup hooks of `connect()` and `sendmsg()`
  replace the Service address with a backend address. The kernel then sends
  the packets to the backend directly. The `recvmsg()` hook puts the Service
  address back into the UDP replies.
- Per-packet LB: the `bpf_lxc` program on the veth of a pod changes the
  destination of each packet to a backend (DNAT). It changes the source of
  each reply back to the Service address.

`cilium-values.yaml` sets `socketLB.hostNamespaceOnly: true`. The chart
writes `bpf-lb-sock-hostns-only: "true"` into the `cilium-config` ConfigMap.
Socket LB then applies only to sockets in the host network namespace. These
are the processes of the node and the `hostNetwork` pods. All other pods get
per-packet LB, as in a cluster with kube-proxy.

### Reason: Kata Containers

A pod with the RuntimeClass `kata-ci` runs in a QEMU virtual machine. See
[Kata Containers for CI Pods](../01-k3s/README.md#kata-containers-for-ci-pods).
The guest kernel owns the sockets of the pod. The socket LB hooks of the
node kernel never see these sockets. With full socket LB, Cilium does not
compile per-packet LB into `bpf_lxc`. Thus, the packets of a Kata pod got no
Service translation.

On 2026-11-27, a Kata pod could not get to any ClusterIP, also not to
kube-dns (`10.43.0.10` and `fd00:cafe:43::a`). Pod addresses and public
addresses worked. A runc pod in the same namespace got to the Services. The
[Cilium documentation](https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/#socket-loadbalancer-bypass-in-pod-namespace)
gives this setting for Kata Containers, KubeVirt and Istio.

### The bpf-lb-sock Value

The chart renders `bpf-lb-sock: "false"` from its default
`socketLB.enabled: false`. With `kube-proxy-replacement: true`, the agent
ignores this value. In Cilium 1.20.5, `NewKPRConfig` in `pkg/kpr/kpr.go`
always enables socket LB in that case. Thus, `cilium-dbg status` shows
`Socket LB: Enabled`. Do not set `socketLB.enabled`. The Cilium example for
this mode also sets only `kubeProxyReplacement` and
`socketLB.hostNamespaceOnly`.

### Changes for Pod Traffic

These changes apply to pods only. The processes of the node and the
`hostNetwork` pods keep socket LB. The file names refer to the source of
Cilium 1.20.5.

- Pod to Service: `bpf_lxc` selects a backend for the first packet. It writes
  a service entry and a connection entry into the conntrack map. Then it
  changes the destination of each packet. For the reply, it changes the
  source back to the Service address. Before, the socket connected to the
  backend directly.
- Conntrack: each pod connection to a Service adds one service entry to the
  conntrack map. The UDP sockets of pods do not add entries to the reverse
  socket maps (`cilium_lb4_reverse_sk`, `cilium_lb6_reverse_sk`) any more.
  The Cilium documentation gives a known exhaustion problem of these maps.
- Session affinity: per-packet LB keeps the affinity for each client
  address. Socket LB kept it for each network namespace. A pod has one
  network namespace and one address for each IP family, so the result is the
  same. On 2026-11-27, no Service in the cluster set `sessionAffinity`.
- Backend removal: Cilium closes UDP sockets to a deleted backend only in
  the host network namespace (`pkg/loadbalancer/reconciler/termination.go`).
  For pod traffic, `bpf_lxc` selects a new backend when the old backend is
  gone (`lb4_local` in `bpf/lib/lb.h`). A TCP connection then normally gets a
  reset. A backend in the `Terminating` state keeps its open connections.
- Service without backends: socket LB gave `EHOSTUNREACH` to `connect()`.
  Per-packet LB sends an ICMP port unreachable reply
  (`service-no-backend-response: reject`). A TCP client then gets
  `ECONNREFUSED`.
- Connection to the own Service: if per-packet LB selects the client pod as
  the backend, it changes the source address to `169.254.42.1` or
  `fe80::1`. These are the defaults of `--ipv4-service-loopback-address`
  and `--ipv6-service-loopback-address`. Cilium skips the ingress policy for
  this connection.
- DSR: no change. DSR (`loadBalancer.mode: dsr` with IP option dispatch)
  applies only to traffic from outside the cluster that arrives on a node
  device (`bpf_host`). Pod traffic to a Service does not use DSR, with or
  without socket LB.
- NodePort and VIP traffic from outside the cluster: no change. `bpf_host`
  on the node device translates it.
- NodePort and VIP traffic from a pod: `bpf_lxc` translates it at the client
  pod, as socket LB did. Since Cilium 1.20, this also applies to a NodePort
  (Cilium upgrade notes). A Service with `externalTrafficPolicy: Local` uses
  its internal scope for this traffic, as before.
- Network policy: no change. `bpf_lxc` translates the Service address
  before the policy check. Thus, Cilium checks the policy against the
  backend, as before.
- DNS proxy: `bpf_lxc` translates `10.43.0.10` to a CoreDNS pod before the
  DNS policy sends the query to the proxy. The proxy sends the query to that
  pod. `bpf_lxc` changes the source of the reply back to `10.43.0.10`. The
  TPROXY path does not change. The node value `src_valid_mark=0` of
  `00-prep` stays necessary.
- Hubble: pod sockets do not send socket translation events
  (`SOCK_XLATE_POINT_*`) any more. These events showed the Service address of
  a pod connection. Sockets in the host network namespace still send them.
  The other flows of a request show the backend pod, as before. With
  `monitor-aggregation: medium`, Cilium does not send `from-endpoint`
  events. A reply flow shows the Service address in `source_xlated`.
- Multiple VIPs: the Cilium known issue "Connection Collisions When a
  Service Endpoint Is Accessed via Multiple VIPs" now also applies to pods.
  The risk is low: a pod must use the same source port for two VIPs of the
  same backend.

### Rollout

1. Run `playbooks/02-cilium/standup.yml`. Helm changes only the
   `cilium-config` ConfigMap and the checksum annotation of the agent
   DaemonSet. The operator and the Envoy DaemonSet do not restart.
2. Wait for the agent rollout. The DaemonSet restarts one agent at a time
   (`maxUnavailable: 1`). Helm waits for all agents, with a timeout of
   1800 s.

A new agent attaches the new socket programs first. Then it compiles the new
`bpf_lxc` and loads it into each endpoint on the node. Between these two
steps, new connections from pods on that node to a Service fail. Open
connections continue. The gap ends when the agent logs
`Finished regenerating restored endpoints`.

The agent becomes ready before the gap ends. The DaemonSet then starts the
next agent. Thus, two nodes can be in the gap at the same time. The agent
logs of the Cilium update on 2026-11-21 give the time between these two
steps. It was 20 s to 21 s on an MS-01 and 31 s to 42 s on a Raspberry Pi.

A pod socket that connected before the change keeps its backend address.
If that backend goes away, Cilium does not close the socket any more. A
restart of the pod clears this state.

Helm does not stop between nodes. Do these checks in a second terminal
while the playbook waits. Do them for each node:

1. Find the agent pod of the node:
   `kubectl -n kube-system get pods -l k8s-app=cilium -o wide`.
2. Wait until this command gives one line:

   ```sh
   kubectl -n kube-system logs <agent-pod> -c cilium-agent | \
     grep 'Finished regenerating restored endpoints'
   ```

3. Make sure that the agent shows the new coverage:

   ```sh
   kubectl -n kube-system exec <agent-pod> -c cilium-agent -- \
     cilium-dbg status --verbose | grep 'Socket LB'
   ```

   The expected output is:

   ```text
     Socket LB:             Enabled
     Socket LB Tracing:     Enabled
     Socket LB Coverage:    Hostns-only
   ```

4. Start a test pod on the node. This pod resolves a name through
   `10.43.0.10` and through `fd00:cafe:43::a`:

   ```sh
   kubectl -n default run svc-check --rm -i --restart=Never \
     --image=busybox:1.37.0 --override-type=strategic --overrides='
   {"spec": {"nodeName": "<node>",
     "securityContext": {"runAsNonRoot": true, "runAsUser": 65534,
       "seccompProfile": {"type": "RuntimeDefault"}},
     "containers": [{"name": "svc-check", "securityContext": {
       "allowPrivilegeEscalation": false, "capabilities": {"drop": ["ALL"]}}}]}}' \
     -- sh -c 'nslookup kubernetes.default.svc.cluster.local 10.43.0.10 &&
       nslookup kubernetes.default.svc.cluster.local fd00:cafe:43::a'
   ```

   The test pod must not use `hostNetwork`. The sockets of a `hostNetwork`
   pod keep socket LB, so this pod does not test `bpf_lxc`. A pod from
   `kubectl debug node/<node>` uses `hostNetwork`. The namespace `default`
   enforces the Pod Security level `restricted`, thus the overrides set the
   security context. Then connect to a ClusterIP Service from a pod of the
   same type.
5. On a node with the `kata-ci` runtime, do step 4 in a Kata pod. Add
   `"runtimeClassName": "kata-ci"` to `spec` in the overrides.
6. Look for new drops of pod traffic:

   ```sh
   kubectl -n kube-system exec <agent-pod> -c cilium-agent -- \
     hubble observe --verdict DROPPED --since 5m
   ```

If a check fails, do the rollback.

### Rollback

1. If the playbook of the rollout still runs, let it finish. Do not
   interrupt it. An interrupt does not stop the agent rollout. Helm does not
   start a second upgrade of the release while the first upgrade waits.
2. Set `socketLB.hostNamespaceOnly: false` in `cilium-values.yaml`.
3. Run `playbooks/02-cilium/standup.yml`. The agents restart one at a time.
4. On each node, make sure that `cilium-dbg status --verbose` shows
   `Socket LB Coverage:    Full`.

After the rollback, Kata pods cannot get to Services. Open pod connections
to a Service stop when `bpf_lxc` loses per-packet LB. Their packets keep the
Service address, and Cilium does not translate them. The clients must
connect again.
