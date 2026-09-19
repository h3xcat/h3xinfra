# MetalLB Resource Budgets

`metallb-values.yaml` uses native chart settings for the controller, speaker,
and active FRR-K8s backend. All eight rendered regular containers receive CPU,
memory, and ephemeral-storage requests and limits. Images, networking,
privileges, replicas, and rollout strategy remain unchanged. Both existing
DaemonSets already roll with an effective `maxUnavailable: 1`.

## Upstream Limitations

MetalLB 0.16.1's bundled FRR-K8s chart does not expose resource settings for
these four init containers:

- `cp-frr-files`
- `cp-reloader`
- `cp-metrics`
- `cp-frr-status`

They remain an explicit unsupported gap, covered by an exact-version render
test. The similarly named `speaker.initContainers` values belong to the
disabled legacy FRR backend and do not affect these containers. No ineffective
values or production post-renderer are added to conceal that gap.

FRR-K8s's status-cleaner Deployment shares `frr-k8s.frrk8s.resources` with its
controller. It inherits the larger controller budget; an independent
status-cleaner budget is not supported by this chart.

For a resource-only maintenance update, pin the deployed chart and use
`--reuse-values` with only the seven resource paths declared in this file.
Compare a server dry run without exposing release values or Secret data.
The observed Helm rendering difference in the FRR startup ConfigMap is exactly
one additional terminal LF in `frr.conf`; any exception must verify unchanged
line content, a one-byte increase, and `new == old + "\n"`. Reject other
non-resource differences.

Focused offline tests:

```bash
helm pull metallb/metallb --version 0.16.1 --untar --untardir /tmp/network-resource-charts
METALLB_CHART_PATH=/tmp/network-resource-charts/metallb \
  python3 -m unittest discover -s playbooks/03-metallb/tests -v
```
