# Longhorn Resource Controls

The Helm release and its defaults are owned by `standup.yml`. Longhorn owns
the CSI Deployments/DaemonSet, instance-manager Pods, recurring CronJobs,
engine-image DaemonSets, and share-manager Pods. Do not patch those generated
objects as a substitute for a durable supported setting.

## Declared Budgets

The manager requests 1 CPU / 3Gi memory and is limited to 4 CPU / 8Gi.
The sizing leaves headroom above the measured seven-day 2.1Gi p95 / 3.8Gi peak.
Its DaemonSet update strategy allows only one unavailable manager at a time.

`defaultSettings.systemManagedCSIComponentsResourceLimits` is a JSON string
mapping native component names to Kubernetes ResourceRequirements. It covers
the attacher, provisioner, resizer, snapshotter, CSI plugin, node registrar,
and liveness probe. Their ephemeral-storage requests/limits are 32Mi / 256Mi.
The Longhorn 1.12.1 parser accepts Kubernetes ResourceRequirements, including
ephemeral storage. The manager is not given an arbitrary disk cap here.

The correct instance-manager key is `guaranteedInstanceManagerCPU`. The
previous `guaranteedInstanceManagerCpu` spelling was ignored by the chart.
The corrected key preserves live percentages: V1 12, V2 31. This is a CPU
reservation, not a CPU limit. No instance-manager memory cap is imposed.

The resource controls leave drain, orphan-data retention, replica counts,
disk free-space policies and engine versions unchanged.

## Attended Failure Recovery

`nodeDownPodDeletionPolicy: do-nothing` disables Longhorn's automatic force
deletion of Pods after node failure. Affected workloads may stay unavailable
until an operator intervenes. This is not fencing: Node NotReady, cordoning,
Pod deletion and watchdog activity do not prove that an old writer stopped.
Require positive shutdown of the old writer or independent fencing before any
forced deletion/detachment or replacement-writer recovery. Do not remove
storage finalizers or replica data to bypass a stalled operation.

RWX share-manager fast failover is unchanged and has separate failure semantics;
this policy does not eliminate every possible dual-writer path. Drain policy
and orphan-data preservation also remain unchanged. Automatic engine-upgrade
concurrency is explicitly zero; upgrades are attended, backed up and serial.

For a setting-only rollout, verify the exact current native settings and
reconcile only the approved saved Helm values at the unchanged chart version.
Require a ConfigMap-only rendered change and check unrelated native defaults
for drift before applying. Do not run the full standup
playbook as a shortcut. Verify the live setting, controller readiness and volume
health after reconciliation. A rollback changes failure handling again and
requires an explicit operational decision.

## Unused V2 Engine

V2 is off by default. At the 2026-09-19 audit there were no V2 volumes, no
block disks, and no engine/replica processes in the three V2 managers. Those
idle managers reserved 6.138 CPU each and ran SPDK polling loops despite every
attached volume using V1.

Before disabling V2, the play refuses to proceed if any V2 volume exists or
any V2 instance manager reports engine/replica processes. The checks are skipped
only before the initial Longhorn CRD installation. Recheck immediately before
a narrowly staged live setting update; do not create V2 volumes concurrently.

For a future intentional V2 deployment, set `longhorn_v2_data_engine: true`
where both `k3s_cluster` and `k8s` inventory hosts receive it. This enables
amd64 hugepage/module preparation as well as the Helm setting. Arm64 nodes
remain excluded. Register suitable block disks and verify prerequisites first.

Disabling V2 removes unused V2 managers, but does not release the host's
existing hugepage reservation or remove kernel module configuration. This
change deliberately leaves those host settings intact.

## Staged Online Rollout

Do not run the whole playbook as a shorthand for a resource-only maintenance
step: it can update the CLI, labels, secrets, chart defaults, CSI components,
and ingress configuration. A full Helm update can start the manager rollout,
CSI restarts, and V2 removal concurrently. Keep the deployed chart version
fixed and compare protected saved release values without printing credentials.

1. Record attached volume health, RW replica counts, active rebuilds, manager
   identities, current settings, and CSI readiness. Require all attached volumes
   healthy with three RW replicas; preserve detached recovery volumes.
2. Verify V2 remains unused. Set only `v2-data-engine=false` through its native
   setting. Wait for unused V2 managers to disappear while V1 managers and all
   attached volumes remain healthy. Never force-delete manager Pods.
3. Roll manager resources with `maxUnavailable=1`. Wait for every manager to
   have the declared resources and be Ready before the next stage.
4. Apply the native CSI resource setting. This restarts affected CSI components;
   provisioning, snapshots, expansion, and attach/detach operations may pause.
   Existing mounted volumes remain usable. Do not run workload relocations or
   node maintenance concurrently. Verify actual container resources and readiness.
5. Recheck all attached volumes and replicas, CSI readiness, application I/O,
   restart/OOM changes and memory/CPU metrics. Reconcile the canonical Helm values
   so future Ansible runs preserve the final native settings.

Stop at any degraded volume, failed rollout, unexpected manager removal, or
resource OOM. Retain the previous values and settings for a narrow rollback.
Do not change failure timeouts, force-detach volumes, delete replica data,
upgrade engines, or clean up referenced engine images to make a rollout finish.

## Explicit Gaps

Longhorn 1.12.1 exposes no resource values for the manager's pre-pull sidecar,
UI, driver deployer/init container, generated engine-image/share-manager Pods,
or recurring-job containers. The RecurringJob and InstanceManager CRDs expose
no generic resource requirements. Engine and replica processes share their
instance-manager container. Blanket LimitRanges can unexpectedly cap active
storage processes and are not used. These gaps need an explicitly supported
extension or upstream change, not hidden live-only patches.

All six installed engine-image generations still had references at audit time.
They must not be deleted merely because the manager/chart version is newer.

## Verification

Run `python -m unittest discover -s playbooks/09-longhorn/tests -v` from the
core repository. Set `LONGHORN_CHART_PATH` to an unpacked deployed-version chart
to include native setting serialization and manager DaemonSet render checks.

References: [Longhorn settings](https://longhorn.io/docs/1.12.1/references/settings/),
[the 1.12.1 settings implementation](https://github.com/longhorn/longhorn-manager/blob/v1.12.1/types/setting.go).
