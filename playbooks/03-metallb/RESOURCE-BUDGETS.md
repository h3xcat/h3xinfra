# MetalLB Resource Budgets

`metallb-values.yaml` uses native chart settings for the controller, the speaker,
and the active FRR-K8s backend. All eight rendered regular containers receive
CPU, memory, and ephemeral-storage requests and limits. Images, networking,
privileges, replicas, and rollout strategy do not change. Both existing
DaemonSets already roll with an effective `maxUnavailable: 1`.

## Upstream Limitations

The FRR-K8s chart that MetalLB 0.16.1 bundles does not give resource settings
for these four init containers:

- `cp-frr-files`
- `cp-reloader`
- `cp-metrics`
- `cp-frr-status`

These containers stay an explicit unsupported gap. An exact-version render test
covers this gap. The `speaker.initContainers` values have a similar name. But
they belong to the disabled legacy FRR backend, and they do not affect these
containers. This configuration adds no ineffective values and no production
post-renderer to conceal that gap.

The status-cleaner Deployment of FRR-K8s shares `frr-k8s.frrk8s.resources` with
its controller. It inherits the larger controller budget. This chart does not
support an independent status-cleaner budget.

For a resource-only maintenance update, do these steps:

1. Pin the deployed chart.
2. Use `--reuse-values` with only the seven resource paths declared in this
   file.
3. Compare a server dry run. Do not expose release values or Secret data.
4. Reject non-resource differences. The only exception is the `frr.conf`
   difference that follows.

The observed Helm rendering difference in the FRR startup ConfigMap is exactly
one more terminal LF in `frr.conf`. To accept this exception, verify these
conditions:

- The line content is unchanged.
- The increase is one byte.
- `new == old + "\n"`.

Reject other non-resource differences.

Run the focused offline tests:

```bash
helm pull metallb/metallb --version 0.16.1 --untar --untardir /tmp/network-resource-charts
METALLB_CHART_PATH=/tmp/network-resource-charts/metallb \
  python3 -m unittest discover -s playbooks/03-metallb/tests -v
```
