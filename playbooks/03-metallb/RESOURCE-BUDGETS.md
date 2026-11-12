# MetalLB Resource Budgets

`metallb-values.yaml` uses native chart settings for the controller, the speaker,
and the active FRR-K8s backend. All eight rendered regular containers receive
CPU, memory, and ephemeral-storage requests and limits. Images, networking,
privileges, replicas, and rollout strategy do not change. Both existing
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
16Mi memory, 32Mi ephemeral storage; limits 100m, 64Mi, 64Mi). The shared
`h3xinfra-private/tests/helm` suite renders the inventory-pinned chart and checks
that every regular and init container has a budget.
MetalLB 0.16.1 (FRR-K8s 0.0.25) did not expose these settings and ignores the
block. The similarly named `speaker.initContainers` values belong to the
disabled legacy FRR backend and do not affect these containers.

The status-cleaner Deployment of FRR-K8s shares `frr-k8s.frrk8s.resources` with
its controller. It inherits the larger controller budget. This chart does not
support an independent status-cleaner budget.

For a resource-only maintenance update, do these steps:

1. Pin the deployed chart.
2. Use `--reuse-values` with only the resource paths in `metallb-values.yaml`.
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

Run the focused offline backend checks:

```bash
python3 -m unittest discover -s playbooks/03-metallb/tests -v
```
