# Cilium Host Firewall

The Cilium host firewall filters traffic to the node itself: the host network
namespace and all `hostNetwork` pods. It is optional and off by default.

## Function

When `standup.yml` applies the layer, it does these steps:

1. It sets `hostFirewall.enabled` on the Cilium chart. The chart writes the
   selected value to `enable-host-firewall` in the `cilium-config` ConfigMap.
2. With `rollOutCiliumPods: true`, the chart puts a checksum of the rendered
   ConfigMap into the agent PodTemplate. A changed checksum triggers a
   DaemonSet rolling update. The agents read `enable-host-firewall` when they
   start. The main Helm upgrade waits for readiness before the next task.
3. It reconciles the `h3xinfra-cilium-post` chart. When enabled, the chart
   adds one CiliumClusterwideNetworkPolicy,
   `<prefix>-post-host-deny-external`.
4. It sets the label `h3xinfra.io/host-firewall=enforce` on each node in
   `cilium.host_firewall.enforce_nodes`. It removes the label from all other
   nodes. The policy selects only labeled nodes.

The policy has one deny rule. It denies ingress to the node from all source
addresses outside the trusted ranges. The trusted ranges are:

- `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` and `169.254.0.0/16`
- `fc00::/7`, `fe80::/10` and `::/128`
- the ranges in `cilium.host_firewall.extra_trusted_cidrs`

The policy sets `enableDefaultDeny` to `false`. Thus the policy does not put
the node into default-deny mode. All traffic that the deny rule does not match
stays allowed.

CIDR rules do not select cluster identities. Traffic from pods, from Cilium
health checks and from node addresses that Cilium knows is not affected.
Cilium knows the addresses in the Node object (for example the `InternalIP`)
and the `cilium_host` addresses. A node can also send traffic from an address
that is not in the Node object, for example a SLAAC or temporary IPv6
address. The deny rule checks this traffic against the trusted ranges. Thus
the global IPv6 prefix of the node network must be a trusted range.

With kube-proxy replacement, traffic to a LoadBalancer or NodePort Service is
checked against the policy of the backend endpoint, not against this policy.

The host firewall does not replace the edge firewall. Keep the router rules
that block inbound traffic.

## Settings

All settings are in `cilium.host_firewall` in `host_vars/k8s/cilium.yml`.

| Setting | Default | Function |
| --- | --- | --- |
| `enabled` | `false` | Loads the host firewall datapath and installs the policy. A changed rendered ConfigMap triggers an agent rollout through the chart checksum. |
| `enforce_nodes` | `[]` | Kubernetes node names that get the label. The list must be empty when `enabled` is `false`. |
| `extra_trusted_cidrs.ipv4` | `[]` | More IPv4 source ranges that the policy does not deny. |
| `extra_trusted_cidrs.ipv6` | `[]` | More IPv6 source ranges that the policy does not deny. Add the global IPv6 prefix of the node network here. |

The playbook checks the settings before it makes a change. It stops if a
setting is not valid or if a name in `enforce_nodes` is not a cluster node.

## Agent rollouts and readiness

The checksum covers the entire rendered `cilium-config`, not only the host
firewall flag. Any rendered ConfigMap change can therefore restart the
agents. Identical rendered configuration produces the same checksum. This is
reconciliation during a Helm upgrade, not a watcher of arbitrary live
ConfigMap edits or agent runtime drift.

The first upgrade that adds the checksum is expected to roll the agents even
when the host firewall remains disabled. This adoption rollout also makes
previously unannotated agents load the desired value, including recovery
from an earlier enabled configuration to `enabled: false`. The old
`h3xinfra.io/host-firewall-enabled` annotation is no longer read or managed by
the playbook. Its removal is neither required for the checksum mechanism nor
proven by omitting it from the new source; do not assume it has been cleaned
up on existing DaemonSets.

Plan the adoption and later configuration changes as separate deployment
operations with a maintenance window, verified recovery access, and agent
readiness and network checks. This source change does not verify the live
agent count, readiness, or rollout duration. The agent DaemonSet retains
`RollingUpdate` with `maxUnavailable: 1`. This is a per-DaemonSet disruption
limit, not a fleetwide sequencing or availability guarantee.

The main Helm task uses `wait: true` and a 1800-second timeout. Pinned Helm
4.3.1 uses its watcher for `--wait`; the DaemonSet check requires the observed
generation and all desired agents updated, available and ready. Preserve
that behavior when changing tooling; `--wait=legacy` has weaker checks. A
no-op Ansible Helm task does not independently wait for live drift to recover.

An agent restart clears host endpoint audit mode. On an already labeled
node, enforcement can resume immediately, before the playbook reconciles the
policy and node labels. This applies to adoption, upgrades and any changed
rendered ConfigMap. Removing a node from `enforce_nodes` alone does not remove
its existing label before the main upgrade. If enforcement must stop first,
use the immediate label-removal procedure below before applying the layer.

## Staged Rollout

Do the enforcement steps in sequence, one node at a time. Before the Cilium
apply, plan the maintenance window and recovery access described above, and
account for any nodes that already carry the enforcement label.

1. Add the global IPv6 prefix of the node network to
   `extra_trusted_cidrs.ipv6`. Do not add a label to a node before you do this
   step. If you do not, the node can drop traffic from other nodes.
2. Add each public source range that must connect directly to a node address
   to `extra_trusted_cidrs`.
3. Set `enabled: true`. Keep `enforce_nodes: []`.
4. Run `ansible-playbook h3xinfra/playbooks/02-cilium/standup.yml`.
   The changed ConfigMap checksum rolls the Cilium agent DaemonSet. Wait for
   all agents to become ready before continuing.
5. Make sure that each agent shows the host firewall:

   ```bash
   for p in $(kubectl -n kube-system get pods -l k8s-app=cilium -o name); do
     kubectl -n kube-system exec "$p" -c cilium-agent -- \
       cilium-dbg status | grep 'Host firewall'
   done
   ```

6. Select one agent node for the first test. Do not select a k3s server node.
   Get the agent pod and the host endpoint ID of that node:

   ```bash
   NODE_NAME=<node>
   CILIUM_POD=$(kubectl -n kube-system get pods -l k8s-app=cilium \
     -o jsonpath="{.items[?(@.spec.nodeName=='$NODE_NAME')].metadata.name}")
   HOST_EP_ID=$(kubectl -n kube-system exec "$CILIUM_POD" -c cilium-agent -- \
     cilium-dbg endpoint list -o jsonpath='{[?(@.status.identity.id==1)].id}')
   ```

7. Optional: set audit mode on the host endpoint of that node before you add
   the label. In audit mode, the agent logs the verdict and does not drop the
   packet.

   ```bash
   kubectl -n kube-system exec "$CILIUM_POD" -c cilium-agent -- \
     cilium-dbg endpoint config "$HOST_EP_ID" PolicyAuditMode=Enabled
   ```

   Audit mode stops when the agent restarts. An already labeled node can
   enforce its existing policy immediately, before policy or label
   reconciliation. Any changed rendered `cilium-config`, as well as adoption
   or an upgrade, can restart the agents. Recheck audit mode after a restart;
   do not rely on the setting from the previous pod.

8. Add the node name to `enforce_nodes`.
9. Run `ansible-playbook h3xinfra/playbooks/02-cilium/standup.yml`.
10. Monitor the policy verdicts on that node:

    ```bash
    kubectl -n kube-system exec "$CILIUM_POD" -c cilium-agent -- \
      cilium-dbg monitor -t policy-verdict --related-to "$HOST_EP_ID"
    ```

11. If a verdict shows `action audit` or `action deny` for traffic that the node
    must accept, add the source range to `extra_trusted_cidrs` and do step 9
    again.
12. If you set audit mode in step 7, set `PolicyAuditMode=Disabled` on the
    same endpoint.
13. Make sure that SSH, the node `Ready` status, Longhorn replicas and
    workloads on the node operate correctly.
14. Do steps 6 to 13 for each remaining node. Do the k3s server nodes last.

## Rollback

To stop enforcement on one node immediately, remove the label:

```bash
kubectl label node <node> h3xinfra.io/host-firewall-
```

Then remove the node from `enforce_nodes`. If you do not, the next playbook run
adds the label again.

To remove the host firewall fully, set `enforce_nodes: []` and
`enabled: false`. Then run `standup.yml` in the planned maintenance window.
The changed rendered ConfigMap checksum rolls the agents to unload the host
firewall datapath. After the main Helm upgrade is ready, the playbook removes
the policy and then the node labels. Remove labels first with the immediate
procedure above if existing enforcement must stop before that upgrade.

Do step 5 of the staged rollout again. Each agent must show that the host
firewall is disabled. Check readiness and connectivity as well. The initial
checksum adoption covers the old unannotated-agent recovery case; later
unchanged Helm inputs do not force a recovery rollout. If an agent still
runs the wrong setting, investigate its runtime state and plan an explicit
recovery rather than assuming another unchanged apply will restart it.

## Known Limits

- When the host firewall is on, the host drops some layer 2 protocols. This
  occurs also when no policy selects the node.
- The host also drops extended IP protocols (for example VRRP, IGMP, GRE and
  ESP), unless the agent has the `--enable-extended-ip-protocols` option.
- For the full list of known issues, refer to the Cilium documentation:
  <https://docs.cilium.io/en/stable/security/policy/host/>.
- When a node first enforces the policy, the node can drop reply traffic of
  existing connections for a short time.
- Host policies do not apply to host WireGuard interfaces.
