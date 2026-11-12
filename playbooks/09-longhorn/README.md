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

The manager requests 1 CPU / 3Gi memory. Its limit is 4 CPU / 8Gi. This size
gives headroom above the measured seven-day values (2.1Gi p95 / 3.8Gi peak).
The DaemonSet update strategy of the manager lets only one manager be
unavailable at a time.

`defaultSettings.systemManagedCSIComponentsResourceLimits` is a JSON string. It
maps native component names to Kubernetes ResourceRequirements. It covers these
components:

- The attacher
- The provisioner
- The resizer
- The snapshotter
- The CSI plugin
- The node registrar
- The liveness probe.

Their ephemeral-storage requests/limits are 32Mi / 256Mi. The Longhorn 1.12.1
parser accepts Kubernetes ResourceRequirements, and these include ephemeral
storage. This configuration does not give the manager an arbitrary disk limit.

The correct instance-manager key is `guaranteedInstanceManagerCPU`. The chart
ignored the previous spelling, `guaranteedInstanceManagerCpu`. The corrected key
keeps the live percentages: V1 12, V2 31. This value is a CPU reservation. It is
not a CPU limit. The configuration sets no memory limit for the instance manager.

The resource controls do not change these items:

- Drain
- Orphan-data retention
- Replica counts
- Disk free-space policies
- Engine versions.

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

## Staged Online Rollout

CAUTION: Do not run the whole playbook as a shorthand for a resource-only
maintenance step. The playbook can update the CLI, labels, secrets, chart
defaults, CSI components, and ingress configuration.

A full Helm update can start the manager rollout, the CSI restarts, and the V2
removal at the same time. Keep the deployed chart version fixed. Compare the
protected saved release values. Do not print credentials.

1. Record these items:
   - Attached volume health
   - RW replica counts
   - Active rebuilds
   - Manager identities
   - Current settings
   - CSI readiness.

   Make sure that all attached volumes are healthy and have three RW replicas.
   Keep the detached recovery volumes.
2. Verify that V2 is still unused. Set only `v2-data-engine=false` through its
   native setting. Wait until the unused V2 managers disappear. Make sure that
   the V1 managers and all attached volumes stay healthy. Do not force-delete
   manager Pods.
3. Roll the manager resources with `maxUnavailable=1`. Wait until every manager
   has the declared resources and is Ready. Then go to the next stage.
4. Apply the native CSI resource setting. This setting restarts the affected CSI
   components. Provisioning, snapshots, expansion, and attach/detach operations
   can pause. Existing mounted volumes stay usable. Do not do workload
   relocations or node maintenance at the same time. Verify the actual container
   resources and readiness.
5. Examine again all attached volumes and replicas, CSI readiness, application
   I/O, restart/OOM changes, and memory/CPU metrics. Reconcile the canonical
   Helm values so that future Ansible runs keep the final native settings.

Stop if one of these conditions occurs:

- A volume is degraded.
- A rollout fails.
- A manager is removed unexpectedly.
- A resource OOM occurs.

Keep the previous values and settings for a narrow rollback.

Do not do these actions to make a rollout finish:

- Change failure timeouts.
- Force-detach volumes.
- Delete replica data.
- Upgrade engines.
- Clean up referenced engine images.

## Explicit Gaps

Longhorn 1.12.1 gives no resource values for these items:

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

References: [Longhorn settings](https://longhorn.io/docs/1.12.1/references/settings/),
[the 1.12.1 settings implementation](https://github.com/longhorn/longhorn-manager/blob/v1.12.1/types/setting.go).
