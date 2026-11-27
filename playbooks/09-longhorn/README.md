# Longhorn Resource Controls

`standup.yml` owns the Helm release and its defaults. Longhorn owns these
generated objects:

- The CSI Deployments/DaemonSet
- The instance-manager Pods
- The CronJobs of the recurring jobs
- The engine-image DaemonSets
- The share-manager Pods

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

The manager budget keeps headroom over the recorded seven-day measurements
(2.1Gi p95 / 3.8Gi peak). Its rollout allows only one unavailable manager.
`defaultSettings.systemManagedCSIComponentsResourceLimits` serializes the CSI
budgets as JSON ResourceRequirements. These also have 32Mi / 256Mi
ephemeral-storage requests/limits for each CSI component. The setting does not
limit manager disk use.

`guaranteedInstanceManagerCPU` reserves 12% CPU for V1 and 31% CPU for V2.
These values are not CPU limits. The configuration sets no instance-manager
memory limit. These items stay separate settings:

- Drain
- Orphan-data retention
- Replica counts
- Disk free-space policies
- Engine versions

## Scheduled Jobs

Each `longhorn.recurring_jobs.jobs` entry renders a RecurringJob. The playbook
gives the job list to Helm through `set_values` as JSON. There is no separate
enable switch. If the list is not empty, the post chart ignores retired
`enabled` values. This also applies to `false`. An empty list creates no
scheduled jobs. If you remove an entry, the next Helm reconciliation removes
that RecurringJob. Volumes join jobs through their recurring-job group labels.

Deploy this chart revision before you remove a retired
`recurring_jobs.enabled` value from an inventory. Older revisions require that
flag to keep the jobs.

## UI Access

The post chart publishes the UI through the shared Gateway. Its SecurityPolicy
(OIDC and/or source CIDRs) gates only that route. The nginx of the UI proxies
the Longhorn API without authentication. Thus, post chart 0.4.0 adds the
NetworkPolicy `<release>-ui-gateway`. With this policy, the `app=longhorn-ui`
pods accept TCP 8000 only from the Envoy proxy pods of
`longhornIngress.gateway`. Other pods cannot reach Service `longhorn-frontend`.
This also applies to pods in the Longhorn namespace. Without a gateway name, the
UI admits no pod.

OIDC alone admits each user of the realm. Post chart 0.5.0 adds an optional
group gate. Set `longhorn.auth.oauth2_proxy.allowed_groups`, for example
`["k8s-admins"]`. Then the OIDC filter forwards the ID token in the header
`X-Oidc-Id-Token`. The JWT filter validates the token, with the client ID as
the audience. The policy admits only a token with one of the groups in its
`groups` claim. The route removes the header before the request goes to the
UI. Before you set the list, set `groups_claim: true` on the Keycloak client
(`10-keycloak`). Without the claim, the gate denies all users. An empty list
keeps the earlier policy.

## Attended Failure Recovery

`nodeDownPodDeletionPolicy: do-nothing` disables the automatic force deletion of
Pods by Longhorn after a node failure. Affected workloads can stay unavailable
until the owner takes action.

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
are serial. The owner attends each engine upgrade and makes a backup before it.

For a setting-only rollout, do these steps:

1. Verify the exact current native settings.
2. Prepare to reconcile only the approved saved Helm values at the unchanged
   chart version.
3. Before you run the reconciliation, make sure that the rendered change is
   ConfigMap-only.
4. Before you run the reconciliation, check the unrelated native defaults for
   drift.
5. Run the reconciliation.
6. Verify the live setting, the controller readiness, and the volume health.

Do not run the full standup playbook as a shortcut. A rollback changes how
Longhorn handles a node failure again. A rollback requires an explicit
operational decision.

## Attended Teardown

Teardown is destructive. The Longhorn 1.13.0 Helm pre-delete Job runs
`longhorn-manager uninstall --force`. This command can delete volumes that are
still in use. The deletion flag allows that Job. It does not protect live data.

Before the owner runs teardown, do these steps:

1. Retire or migrate every Longhorn workload.
2. Review the PV/PVC retention.
3. Make sure that the necessary backups and a tested recovery path exist.

Keep this work separate from normal setting updates and from failure recovery.

`teardown.yml` still requires `-e confirm_destroy=yes-destroy-data` before any
connection or mutation. It rejects incomplete releases, releases with an open
Helm operation, and orphaned Longhorn CRDs. Then it patches the
`deleting-confirmation-flag` Setting, but only if this Setting exists. The
standup playbook never enables the flag.

Helm removes the releases in this order: post, main, pre. The main release
keeps its supported uninstall hook. It has a 20-minute timeout for the 15-minute
Job deadline of the chart. Any failure stops later plays. If Longhorn CRDs
remain, the playbook does not remove the pre-release and the namespace. An
installation that is already absent needs no Setting creation and no finalizer
bypass.

Before an attended run, review the current release state and the chart version
of the release. A confirmed `--check` run can preview tasks. But it cannot
prove the uninstall Job, the volume retirement or the recovery. It skips its
post-uninstall CRD check, because no deletion occurred. After a real run,
verify the removal of the release, the CRDs and the namespace. Also verify the
planned data-retention result.

If the main uninstall fails, keep the pre-release and the namespace. Then check
the Job and the release before you try again. If the flag patch succeeds and a
later step fails, the flag can stay true. Check the active uninstall work
before you decide to cancel or reset it. For an incomplete installation, use
the Longhorn recovery procedure for the same version. This procedure can
require that you restore its release before the uninstall.

To force completion, do not do these actions:

- Remove finalizers.
- Delete webhooks.
- Delete replica data.

A revert of this source change cannot restore deleted data or reset the live
flag.

References: [Longhorn 1.13.0 uninstall procedure](https://longhorn.io/docs/1.13.0/deploy/uninstall/),
[the pinned Helm uninstall Job](https://github.com/longhorn/charts/blob/longhorn-1.13.0/charts/longhorn/templates/uninstall-job.yaml),
and [the pinned uninstall controller](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/controller/uninstall_controller.go).

## Parked V2 Engine

V2 and dedicated block disks stay parked. `standup.yml` sets the literal
`v2DataEngine: false`. The retired inventory opt-in no longer enables V2.
The V1/V2 instance-manager CPU reservations stay at 12%/31%.

Before the play changes the setting, it requires one of these states:

- A clean first install. Both the Volume CRD and the InstanceManager CRD are
  absent.
- Both APIs are available.

The play stops if a V2 volume exists. It also stops if a V2 engine, frontend,
replica, shard or shard-group process exists. A real run waits until the native
setting is false and the V2 instance managers are gone. Check mode keeps the
preflight but skips those waits. Thus, it cannot prove that the setting changed
or that the processes stopped. To finish a rollout, do not bypass these checks.
Do not force-delete managers.

Post chart 0.3.0 removes its V2 StorageClass, normally `longhorn-v2`. Before
you reconcile that chart, check the saved release values for a customized class
name. Then verify that no PVC, PV or workload configuration references the
class. This also applies to Pending claims. Check V2 volumes, block disks and
backing-image data separately. An empty process map does not prove that the
stored data is disposable. Pause provisioning writers during this review and
reconciliation. If a consumer or V2 data exists, stop. Then plan its migration
with the owner. A deletion of the class does not migrate volumes or restore
data.

Prepare a narrow post-chart reconciliation at the unchanged upstream version.
Review the rendered deletion and the harmless chart-version label changes.
Before the attended change, verify the storage health and the backups. After
the change, verify these results:

- The class is absent.
- V1 volumes stay healthy.
- The scheduled jobs did not change.

Do not run the full standup or prep playbook as a shortcut for the node
cleanup.

The removal of the preparation from the source does not do these actions:

- Delete installed files.
- Unload modules.
- Release reserved memory.
- Remove the arm64 disable labels that exist.

Keep those labels until a separately reviewed future V2 design. Node cleanup is
a separate attended operation after the source change:

1. Verify that V2 is disabled and that it has no volumes, instance managers or
   instance-manager Pods.
2. Make a list of the other hugepage and kernel-module consumers. Empty process
   maps alone do not prove that an idle or unreachable manager released its
   hugepages.
3. For recovery, record the current sysctl, boot configuration and module files
   of each node.
4. On the amd64 nodes that used V2 before, remove only the reviewed retired
   files `/etc/sysctl.d/99-longhorn-v2.conf` and
   `/etc/modules-load.d/longhorn-v2.conf`.
5. Release runtime hugepages only after you verify that there are no consumers
   and no other reservation sources.
6. On the affected nodes, check the consumers of the retired `vfio_pci.conf`,
   `uio_pci_generic.conf` and `nvme-tcp.conf` module-load files.
7. After this check, remove these files. Do not automatically unload active
   modules.
8. Verify the reservation and the available memory on each node after the
   change. The earlier source reserved 1,024 two-MiB pages (2 GiB). Installed
   values can differ. Thus, do not assume that the change freed a fixed amount
   of memory.
9. Wait for the kubelet capacity to refresh at the next planned reboot. Do not
   restart k3s-agent to force a refresh.

Keep the Pi kernel-command-line migration and the crash-capture hugepage
overrides. There are two reasons. Retired files can remain until the cleanup.
Other hugepage settings can still reach the capture kernel. If a consumer or a
degraded volume appears, stop. Then preserve the data. Restore reviewed node files and settings
only through an attended recovery plan. If you revert the source or create the
StorageClass again, this does not restore data. It also does not safely enable
V2 again. Future V2 use needs a new design and new prerequisites.

The guards follow the [pinned V2 setting validation](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/datastore/longhorn.go)
and [instance-manager status fields](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/k8s/pkg/apis/longhorn/v1beta2/instancemanager.go).
They are not a lock against concurrent changes. Healthy API access and an
attended inventory stay prerequisites.

## Resource Maintenance

Git history keeps the
[earlier staged rollout sequence](https://github.com/coderpunch/h3xinfra/blob/e2d8d615fa67f1449bee398308311ad6788ec050/playbooks/09-longhorn/README.md#staged-online-rollout).
Plan future changes against the current settings, the saved release values and
the volume health. For resource-only maintenance, keep the chart version fixed.
Do not run the whole standup as a shortcut. For a narrow rollback, preserve the
detached recovery volumes and the previous values. Do not print credentials.

Stop on a degraded volume, a failed rollout, an unexpected manager removal or a
resource OOM. To make a rollout finish, do not do these actions:

- Change failure timeouts.
- Force-detach volumes.
- Delete replica data.
- Upgrade engines.
- Clean up referenced engine images.

Keep workload relocations and node maintenance separate from CSI restarts.
Before you continue, verify the readiness, the replica health and the
application I/O.

## Explicit Gaps

Longhorn 1.13.0 gives no resource values for these items:

- The pre-pull sidecar of the manager
- The UI
- The driver deployer/init container
- The generated engine-image/share-manager Pods
- The containers of the recurring jobs

The RecurringJob and InstanceManager CRDs give no generic resource requirements.
Engine and replica processes share their instance-manager container. Blanket
LimitRanges can unexpectedly limit active storage processes. This configuration
does not use them. To close these gaps, use an explicitly supported extension or
an upstream change. Do not use hidden live-only patches.

At audit time, all six installed engine-image generations still had references.
Do not delete them only because the manager/chart version is newer.

## Longhorn CLI cleanup

The playbooks no longer install or remove `longhornctl`. Before, they ran it
only for its version check. Current nodes keep the binary until the owner
removes `/usr/local/bin/longhornctl`. Before this removal, the owner checks for
local maintenance scripts that use the binary. Do this removal separately from
a storage rollout. The volume and instance-manager checks, the CSI settings,
and the authentication guards stay in `standup.yml`.

## Verification

From the core repository, run
`python -m unittest discover -s playbooks/09-longhorn/tests -v` for the storage,
rollout, authentication, UI ingress and V2 safety checks. The shared
`h3xinfra-private/tests/helm` suite renders the inventory-pinned Longhorn chart
and checks manager/container budgets and native CSI, CPU and V2 settings.

References: [Longhorn settings](https://longhorn.io/docs/1.13.0/references/settings/),
[the 1.13.0 settings implementation](https://github.com/longhorn/longhorn-manager/blob/v1.13.0/types/setting.go).
