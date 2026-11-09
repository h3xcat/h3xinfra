# MetalLB Resource Budgets

`metallb-values.yaml` uses native chart settings for the controller, speaker,
and active FRR-K8s backend. All eight rendered regular containers receive CPU,
memory, and ephemeral-storage requests and limits. Images, networking,
privileges, replicas, and rollout strategy remain unchanged. Both existing
DaemonSets already roll with an effective `maxUnavailable: 1`.

## FRR-K8s Init Containers

MetalLB 0.16.2 bundles FRR-K8s 0.0.27. That chart exposes resource settings for
the four init containers of the FRR-K8s DaemonSet, under
`frr-k8s.frrk8s.initContainers.<name>.resources`:

- `cp-frr-files` (`cpFrrFiles`)
- `cp-reloader` (`cpReloader`)
- `cp-metrics` (`cpMetrics`)
- `cp-frr-status` (`cpFrrStatus`)

They only copy files, so they get the reloader-sized budget (requests 10m CPU,
16Mi memory, 32Mi ephemeral storage; limits 100m, 64Mi, 64Mi). An exact-version
render test checks that every regular and init container has a budget.
MetalLB 0.16.1 (FRR-K8s 0.0.25) did not expose these settings and ignores the
block. The similarly named `speaker.initContainers` values belong to the
disabled legacy FRR backend and do not affect these containers.

FRR-K8s's status-cleaner Deployment shares `frr-k8s.frrk8s.resources` with its
controller. It inherits the larger controller budget; an independent
status-cleaner budget is not supported by this chart.

For a resource-only maintenance update, pin the deployed chart and use
`--reuse-values` with only the resource paths declared in `tests/test_resources.py`.
Compare a server dry run without exposing release values or Secret data.
The observed Helm rendering difference in the FRR startup ConfigMap is exactly
one additional terminal LF in `frr.conf`; any exception must verify unchanged
line content, a one-byte increase, and `new == old + "\n"`. Reject other
non-resource differences.

Focused offline tests:

```bash
helm pull metallb/metallb --version 0.16.2 --untar --untardir /tmp/network-resource-charts
METALLB_CHART_PATH=/tmp/network-resource-charts/metallb \
  python3 -m unittest discover -s playbooks/03-metallb/tests -v
```
