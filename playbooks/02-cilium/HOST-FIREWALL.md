# Cilium Host Firewall

The Cilium host firewall filters traffic to the node itself: the host network
namespace and all `hostNetwork` pods. It is optional and off by default.

## Function

When you turn it on, `standup.yml` does these steps:

1. It sets `hostFirewall.enabled=true` on the Cilium agent.
2. It installs the `h3xinfra-cilium-post` chart. The chart adds one
   CiliumClusterwideNetworkPolicy, `<prefix>-post-host-deny-external`.
3. It sets the label `h3xinfra.io/host-firewall=enforce` on each node in
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

CIDR rules do not select cluster identities. Traffic from other nodes, from
pods and from Cilium health checks is not affected. With kube-proxy
replacement, traffic to a LoadBalancer or NodePort Service is checked against
the policy of the backend endpoint, not against this policy.

The host firewall does not replace the edge firewall. Keep the router rules
that block inbound traffic.

## Settings

All settings are in `cilium.host_firewall` in `host_vars/k8s/cilium.yml`.

| Setting | Default | Function |
| --- | --- | --- |
| `enabled` | `false` | Loads the host firewall datapath and installs the policy. A change restarts the Cilium agents, one node at a time. |
| `enforce_nodes` | `[]` | Kubernetes node names that get the label. The list must be empty when `enabled` is `false`. |
| `extra_trusted_cidrs.ipv4` | `[]` | More IPv4 source ranges that the policy does not deny. |
| `extra_trusted_cidrs.ipv6` | `[]` | More IPv6 source ranges that the policy does not deny. Add the global IPv6 prefix of the LAN here. |

The playbook checks the settings before it makes a change. It stops if a
setting is not valid or if a name in `enforce_nodes` is not a cluster node.

## Staged Rollout

Do the steps in sequence. Do one node at a time.

1. Add each public source range that must connect directly to a node address
   to `extra_trusted_cidrs`. Include the global IPv6 prefix of the LAN.
2. Set `enabled: true`. Keep `enforce_nodes: []`.
3. Run `ansible-playbook h3xinfra/playbooks/02-cilium/standup.yml`.
4. Make sure that all Cilium agents are ready.
5. Make sure that each agent shows the host firewall:

   ```bash
   kubectl -n kube-system exec ds/cilium -- cilium-dbg status | grep 'Host firewall'
   ```

6. Select one agent node for the first test. Do not select a k3s server node.
7. Optional: set audit mode on the host endpoint of that node before you add
   the label. In audit mode, the agent logs the verdict and does not drop the
   packet.

   ```bash
   NODE_NAME=<node>
   CILIUM_POD=$(kubectl -n kube-system get pods -l k8s-app=cilium \
     -o jsonpath="{.items[?(@.spec.nodeName=='$NODE_NAME')].metadata.name}")
   HOST_EP_ID=$(kubectl -n kube-system exec "$CILIUM_POD" -- \
     cilium-dbg endpoint list -o jsonpath='{[?(@.status.identity.id==1)].id}')
   kubectl -n kube-system exec "$CILIUM_POD" -- \
     cilium-dbg endpoint config "$HOST_EP_ID" PolicyAuditMode=Enabled
   ```

   Audit mode stops when the agent restarts. After a restart, the agent
   enforces the policy immediately.

8. Add the node name to `enforce_nodes`.
9. Run `ansible-playbook h3xinfra/playbooks/02-cilium/standup.yml`.
10. Monitor the policy verdicts on that node:

    ```bash
    kubectl -n kube-system exec "$CILIUM_POD" -- \
      cilium-dbg monitor -t policy-verdict --related-to "$HOST_EP_ID"
    ```

11. If a verdict shows `action audit` or `action deny` for traffic that the node
    must accept, add the source range to `extra_trusted_cidrs` and do step 9
    again.
12. If you set audit mode in step 7, set `PolicyAuditMode=Disabled` on the
    same endpoint.
13. Make sure that SSH, the node `Ready` status, Longhorn replicas and
    workloads on the node operate correctly.
14. Do steps 7 to 13 for each remaining node. Do the k3s server nodes last.

## Rollback

To stop enforcement on one node immediately, remove the label:

```bash
kubectl label node <node> h3xinfra.io/host-firewall-
```

Then remove the node from `enforce_nodes`. If you do not, the next playbook run
adds the label again.

To remove the host firewall fully, set `enforce_nodes: []` and
`enabled: false`. Then run `standup.yml`.

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
