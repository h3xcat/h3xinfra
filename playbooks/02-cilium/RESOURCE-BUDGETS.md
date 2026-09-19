# Cilium Resource Budgets

`cilium-values.yaml` declares CPU, memory, and ephemeral-storage requests and
limits for the agent, its init/mount helpers, Envoy, the operator, Hubble relay
and UI, and certificate generation. Budgets use the observed seven-day workload
envelope with burst headroom; they do not change images, networking, privileges,
or replica counts.

The agent and Envoy DaemonSets each change from `maxUnavailable: 2` to `1`.
This limit is per DaemonSet, not a global node lock: independent DaemonSet
controllers can update different nodes concurrently. Stage the two updates
separately when maintenance requires a fleet-wide one-node-at-a-time guarantee.

The upstream chart includes an ordinary, non-hook certificate Job whose name
contains a checksum of its pod specification. Adding `certgen.resources`
replaces that Job and runs certificate generation once; `--no-hooks` does not
suppress it. The existing CA is reused through `--ca-reuse-secret`. Verify the
CA Secret data is unchanged and the replacement Job completes; leaf
certificates can be reissued as part of this normal chart behavior.

For an existing deployment, pin the currently deployed chart version and use
`--reuse-values` with only the resource paths and the two update-strategy paths.
Do not replay the complete example values file over live CIDRs or other
inventory-specific settings. Parse a server dry run privately and reject any
changes outside those fields and the certificate Job checksum name. Never
print the full release values, manifests, or Secret data.

Focused offline tests:

```bash
helm pull cilium/cilium --version 1.20.1 --untar --untardir /tmp/network-resource-charts
CILIUM_CHART_PATH=/tmp/network-resource-charts/cilium \
  python3 -m unittest discover -s playbooks/02-cilium/tests -v
```

The exact-chart tests check all 14 regular/init/Job/CronJob container
declarations and compare every other rendered field, permitting only the two
explicit rollout limits and the certificate Job name change.
