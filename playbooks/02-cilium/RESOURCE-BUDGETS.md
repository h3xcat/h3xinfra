# Cilium Resource Budgets

`cilium-values.yaml` declares CPU, memory, and ephemeral-storage requests and
limits for these components:

- The agent and its init/mount helpers
- Envoy
- The operator
- Hubble relay and UI
- Certificate generation.

The budgets use the observed seven-day workload envelope with burst headroom.
They do not change images, networking, privileges, or replica counts.

The agent and Envoy DaemonSets each change from `maxUnavailable: 2` to `1`. This
limit applies to each DaemonSet. It is not a global node lock. Independent
DaemonSet controllers can update different nodes at the same time. If
maintenance requires a fleet-wide one-node-at-a-time guarantee, stage the two
updates separately.

The upstream chart includes an ordinary, non-hook certificate Job. The name of
this Job contains a checksum of its pod specification. When you add
`certgen.resources`, the chart replaces that Job, and certificate generation runs
once. `--no-hooks` does not stop this. The existing CA is reused through
`--ca-reuse-secret`. Verify that the CA Secret data does not change and that the
replacement Job completes. This normal chart behavior can reissue leaf
certificates.

For an existing deployment, do these steps:

1. Pin the chart version that is currently deployed.
2. Use `--reuse-values` with only the resource paths and the two update-strategy
   paths. Do not replay the complete example values file over live CIDRs or
   other inventory-specific settings.
3. Parse a server dry run privately.
4. Reject all changes outside those fields and the certificate Job checksum
   name.

Do not print the full release values, manifests, or Secret data.

Run the focused offline tests:

```bash
helm pull cilium/cilium --version 1.20.1 --untar --untardir /tmp/network-resource-charts
CILIUM_CHART_PATH=/tmp/network-resource-charts/cilium \
  python3 -m unittest discover -s playbooks/02-cilium/tests -v
```

The exact-chart tests check all 14 regular/init/Job/CronJob container
declarations. They also compare every other rendered field. They permit only the
two explicit rollout limits and the certificate Job name change.
