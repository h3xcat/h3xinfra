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

## Attended Teardown

Teardown is destructive: the Longhorn 1.13.0 Helm pre-delete Job invokes
`longhorn-manager uninstall --force`, which can remove volumes still in use.
The deletion flag permits that Job; it does not protect live data. Before an
owner runs teardown, retire or migrate every Longhorn workload, review PV/PVC
retention, and establish the required backups and tested recovery path. Keep
this work separate from ordinary setting updates or failure recovery.

`teardown.yml` still requires `-e confirm_destroy=yes-destroy-data` before any
connection or mutation. It rejects pending/incomplete releases and orphaned
Longhorn CRDs, then patches only an existing `deleting-confirmation-flag`
Setting. The flag is never enabled by standup. Helm removes post, main, then
pre; the main release retains its supported uninstall hook and has a
20-minute timeout for the chart's 15-minute Job deadline. Any failure stops
later plays. Remaining Longhorn CRDs stop pre-release and namespace removal.
An already absent installation needs no Setting creation or finalizer bypass.

Review the current release state and matching chart version before an attended
run. A confirmed `--check` can preview tasks but cannot prove the uninstall
Job, volume retirement, or recovery; its post-uninstall CRD check is skipped
because no deletion has happened. After a real run, verify release/CRD and
namespace removal and the planned data-retention outcome.

If the main uninstall fails, preserve the pre-release and namespace and inspect
the Job and release before retrying. A successful flag patch followed by failure can
leave the flag true. Inspect active uninstall work before deciding whether to
cancel or reset it. For an incomplete installation, use the version-matched
Longhorn recovery procedure, which can require restoring its release before
uninstalling. Do not strip finalizers, delete webhooks, or remove replica data
to force completion. Reverting this source change cannot restore deleted data
or reset the live flag.

References: [Longhorn 1.13.0 uninstall procedure](https://longhorn.io/docs/1.13.0/deploy/uninstall/),
[the pinned Helm uninstall Job](https://github.com/longhorn/charts/blob/longhorn-1.13.0/charts/longhorn/templates/uninstall-job.yaml),
and [the pinned uninstall controller](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/uninstall_controller.go).

## Parked V2 Engine

V2 and dedicated block disks are parked. `standup.yml` sets the literal
`v2DataEngine: false`; the former inventory opt-in no longer enables V2.
The V1/V2 instance-manager CPU reservations remain 12%/31%.

Before changing the setting, the play requires either a clean first install
(both Volume and InstanceManager CRDs absent) or both APIs available. It stops
if any V2 volume or V2 engine, frontend, replica, shard or shard-group process
exists. A real run waits for the native setting to be false and V2 instance
managers to disappear. Check mode retains preflight but skips those waits;
it cannot prove the setting changed or processes stopped. Do not bypass these
checks or force-delete managers to finish a rollout.

Post chart 0.3.0 removes its V2 StorageClass, normally `longhorn-v2`. Before
reconciling that chart, inspect saved release values for a customized class
name, and verify that no PVC (including Pending claims), PV or workload
configuration references it. Inspect V2 volumes, block disks and backing-image
data separately; an empty process map does not prove stored data is disposable.
Pause provisioning writers during this review and reconciliation. Stop if a
consumer or V2 data exists and plan its migration with the owner. Deleting the
class does not migrate volumes or provide data recovery.

Prepare a narrow post-chart reconciliation at the unchanged upstream version;
review the rendered deletion and the harmless chart-version label changes.
Verify storage health and backups before the attended change, then confirm the
class is absent, V1 volumes remain healthy, and scheduled jobs are unchanged.
Do not run the full standup or prep playbook as a host-cleanup shortcut.

Removing preparation from source does not delete installed files, unload
modules, release reserved memory, or remove existing arm64 disable labels.
Keep those labels until a separately reviewed future V2 design. Host cleanup
is a separate attended operation after the source change:

1. Confirm V2 is disabled and has no volumes, instance managers or their Pods;
   inventory other hugepage and kernel-module consumers. Empty process maps
   alone do not prove an idle or unreachable manager has released its hugepages.
   Record each node's current sysctl, boot configuration and module files for
   recovery.
2. On the former amd64 V2 nodes, remove only the reviewed legacy
   `/etc/sysctl.d/99-longhorn-v2.conf` and
   `/etc/modules-load.d/longhorn-v2.conf`. Release runtime hugepages only after
   confirming there are no consumers and no other reservation sources.
3. On affected nodes, remove the obsolete `vfio_pci.conf`,
   `uio_pci_generic.conf` and `nvme-tcp.conf` module-load files only after checking
   their consumers. Do not automatically unload active modules.
4. Verify the resulting reservation and available memory per node. The former
   source reserved 1,024 two-MiB pages (2 GiB); installed values can differ, so
   do not assume a fixed amount of memory was freed. Let kubelet capacity refresh
   with the next planned reboot; do not restart k3s-agent to force a refresh.

Keep Pi kernel-command-line migration and crash-capture hugepage overrides:
legacy files can remain until cleanup, and other hugepage settings can still
reach the capture kernel. If a consumer or degraded volume appears, stop and
preserve data. Restore reviewed host files/settings only through an attended
recovery plan. Reverting source or recreating the StorageClass does not restore
data or safely re-enable V2; future V2 use needs a new design and prerequisites.

The guards follow the [pinned V2 setting validation](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/datastore/longhorn.go)
and [instance-manager status fields](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/k8s/pkg/apis/longhorn/v1beta2/instancemanager.go).
They are not a lock against concurrent changes; healthy API access and an
attended inventory remain prerequisites.

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

## Longhorn CLI cleanup

The playbooks no longer install or remove `longhornctl`; their only invocation
was its version check. Existing nodes keep the binary until the owner removes
`/usr/local/bin/longhornctl` after checking for local maintenance-script users.
Do that separately from a storage rollout. The volume and instance-manager
checks, CSI settings, and authentication guards remain in
`standup.yml`.

## Verification

From the core repository, run
`python -m unittest discover -s playbooks/09-longhorn/tests -v` for the storage,
rollout, authentication and V2 safety checks. The shared
`h3xinfra-private/tests/helm` suite renders the inventory-pinned Longhorn chart
and checks manager/container budgets and native CSI, CPU and V2 settings.

References: [Longhorn settings](https://longhorn.io/docs/1.13.0/references/settings/),
[the 1.13.0 settings implementation](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/types/setting.go).
