# Longhorn Resource Controls

`standup.yml` owns the Helm release and its defaults. Longhorn owns these
generated objects:

- The CSI Deployments/DaemonSet
- The instance-manager Pods
- The recurring CronJobs
- The engine-image DaemonSets
- The share-manager Pods.

Do not patch these generated objects in place of a durable, supported setting.

## Declared Budgets

`standup.yml` declares these Longhorn 1.13.0 resource controls:

| Component | Requests | Limits |
| --- | --- | --- |
| Manager | 1 CPU / 3Gi memory | 4 CPU / 8Gi memory |
| Global manager | 100m CPU / 128Mi memory | 1 CPU / 512Mi memory |
| CSI attacher, provisioner, resizer and snapshotter | 50m CPU / 64Mi memory | 500m CPU / 256Mi memory |
| CSI plugin | 100m CPU / 128Mi memory | 1 CPU / 512Mi memory |
| Node registrar and liveness probe | 25m CPU / 32Mi memory | 250m CPU / 128Mi memory |

The manager budget retains headroom over the recorded seven-day measurements
(2.1Gi p95 / 3.8Gi peak), and its rollout allows only one unavailable manager.
`defaultSettings.systemManagedCSIComponentsResourceLimits` serializes the CSI
budgets as JSON ResourceRequirements, including 32Mi / 256Mi ephemeral-storage
requests/limits for each CSI component. It does not limit manager disk use.

`guaranteedInstanceManagerCPU` reserves V1 12% and V2 31% CPU; these are not CPU
limits, and no instance-manager memory limit is set. Drain, orphan-data
retention, replica counts, disk free-space policies and engine versions remain
separate settings.

## Scheduled Jobs

Each `longhorn.recurring_jobs.jobs` entry renders a RecurringJob. The job list
continues through `set_values` as JSON; there is no separate enable switch.
Legacy `enabled` values, including `false`, are ignored when the list is nonempty.
An empty list creates no scheduled jobs, and removing an existing entry removes
that RecurringJob on the next Helm reconciliation. Volumes join jobs through
their recurring-job group labels.

Install this chart revision before removing a legacy `recurring_jobs.enabled`
value from an inventory: older revisions require that flag to retain the jobs.

## Attended Failure Recovery

`nodeDownPodDeletionPolicy: do-nothing` disables the automatic force deletion of
Pods by Longhorn after a node failure. Affected workloads can stay unavailable
until an operator takes action.

This policy is not fencing. These events do not prove that an old writer
stopped:

- Node NotReady
- A node cordon
- Pod deletion
- Watchdog activity.

Before any forced deletion/detachment or replacement-writer recovery, get a
positive shutdown of the old writer or use independent fencing. Do not remove
storage finalizers or replica data to bypass a stalled operation.

This policy does not change the RWX share-manager fast failover. That failover
has separate failure semantics. This policy does not remove every possible
dual-writer path. The drain policy and orphan-data preservation also stay the
same. Automatic engine-upgrade concurrency is explicitly zero. Engine upgrades
are attended, backed up, and serial.

For a setting-only rollout, do these steps:

1. Verify the exact current native settings.
2. Prepare to reconcile only the approved saved Helm values at the unchanged
   chart version.
3. Before you apply, make sure that the rendered change is ConfigMap-only.
4. Before you apply, examine the unrelated native defaults for drift.
5. Apply the reconciliation.
6. Verify the live setting, the controller readiness, and the volume health.

Do not run the full standup playbook as a shortcut. A rollback changes the
failure handling again. A rollback requires an explicit operational decision.

## Unused V2 Engine

V2 is off by default. The audit on 2026-09-19 found no V2 volumes, no block
disks, and no engine/replica processes in the three V2 managers. Each of those
idle managers reserved 6.138 CPU and ran SPDK polling loops, although every
attached volume used V1.

Before the play disables V2, it does these checks:

- If a V2 volume exists, the play stops.
- If a V2 instance manager reports engine/replica processes, the play stops.

The play skips these checks only before the initial Longhorn CRD installation.
Do these checks again immediately before a narrowly staged live setting update.
Do not create V2 volumes at the same time.

For a future intentional V2 deployment, do these steps:

1. Register suitable block disks.
2. Verify the prerequisites.
3. Set `longhorn_v2_data_engine: true` where both `k3s_cluster` and `k8s`
   inventory hosts receive it.

This setting enables the amd64 hugepage/module preparation and the Helm setting.
Arm64 nodes stay excluded.

When you disable V2, the unused V2 managers are removed. But the host keeps its
existing hugepage reservation and its kernel module configuration. This change
intentionally does not change those host settings.

## Resource Maintenance

The [earlier staged rollout sequence](https://github.com/h3xcat/h3xinfra/blob/e2d8d615fa67f1449bee398308311ad6788ec050/playbooks/09-longhorn/README.md#staged-online-rollout)
is archived in Git history. Plan future
changes against the current settings, saved release values and volume health;
keep the chart version fixed for resource-only maintenance and do not run the
whole standup as a shortcut. Preserve detached recovery volumes and previous
values for a narrow rollback, and do not print credentials.

Stop on a degraded volume, failed rollout, unexpected manager removal or
resource OOM. Do not change failure timeouts, force-detach volumes, delete
replica data, upgrade engines or clean up referenced engine images to make a
rollout finish. Keep workload relocations and node maintenance separate from
CSI restarts, and verify readiness, replica health and application I/O before
continuing.

## Explicit Gaps

Longhorn 1.13.0 gives no resource values for these items:

- The pre-pull sidecar of the manager
- The UI
- The driver deployer/init container
- The generated engine-image/share-manager Pods
- The recurring-job containers.

The RecurringJob and InstanceManager CRDs give no generic resource requirements.
Engine and replica processes share their instance-manager container. Blanket
LimitRanges can unexpectedly limit active storage processes. This configuration
does not use them. To close these gaps, use an explicitly supported extension or
an upstream change. Do not use hidden live-only patches.

At audit time, all six installed engine-image generations still had references.
Do not delete them only because the manager/chart version is newer.

## Verification

From the core repository, run
`python -m unittest discover -s playbooks/09-longhorn/tests -v` for the storage,
rollout, authentication and V2 safety checks. The shared
`h3xinfra-private/tests/helm` suite renders the inventory-pinned Longhorn chart
and checks manager/container budgets and native CSI, CPU and V2 settings.

References: [Longhorn settings](https://longhorn.io/docs/1.13.0/references/settings/),
[the 1.13.0 settings implementation](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/types/setting.go).
