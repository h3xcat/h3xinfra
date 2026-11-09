# Packaged Add-on Resource Exceptions

K3s continues to own CoreDNS, metrics-server, and local-path-provisioner. This
change intentionally does not do these actions for these components:

- Patch their Deployments
- Edit packaged manifests
- Create `.skip` markers
- Pass `--disable`

Exception: `coredns.yml` sets the replica count of the CoreDNS Deployment
through the scale subresource. It also adds a separate PodDisruptionBudget.
It does not change the K3s manifest. K3s continues to own CoreDNS.

## Verified State

The production inventory and the live control-plane labels agree. The K3s servers
are **h3xpi01, h3xpi02, and h3xpi03**. The other two Pis and all three x86 nodes
are agents. The management of server manifests must target the `server` inventory group.
It must not target all Pis or the full cluster.

These components are plain `Addon` resources. They are not `HelmChart` releases with resource-value
overrides. On 2026-09-19, each Deployment had one available replica. The table shows the values:

| Container | Existing requests | Existing limits | Proposed requests | Proposed limits |
| --- | --- | --- | --- | --- |
| CoreDNS | 100m CPU, 70Mi memory | 170Mi memory | 100m CPU, 128Mi memory | 1 CPU, 512Mi memory |
| metrics-server | 100m CPU, 70Mi memory | None | 100m CPU, 128Mi memory | 1 CPU, 512Mi memory |
| local-path-provisioner | None | None | 25m CPU, 64Mi memory | 500m CPU, 256Mi memory |

The proposed ephemeral-storage request/limit is 32Mi/256Mi for each container.
These values are future targets, **not deployed budgets**.

The seven-day maxima were these values:

- CoreDNS: memory working set about 83Mi, CPU rate 9m
- metrics-server: memory working set about 94Mi, CPU rate 16m
- local-path-provisioner: memory working set about 47Mi, CPU rate 0.4m

These Deployments had no init containers.

## Why Ownership Has Not Changed

The [K3s packaged-component documentation](https://docs.k3s.io/installation/packaged-components)
states that K3s writes the packaged manifests again at each server start. A `.skip`
marker keeps the existing objects. But it also stops K3s from applying the subsequent packaged updates.
`--disable` actively uninstalls the component. Thus, it is not a
correct ownership-transfer mechanism for this resource-only maintenance.

The `bin/h3xinfra-deploy-stack` script of the repository imports `01-k3s/standup.yml`.
That playbook imports `k3s.orchestration.site` from the pinned 1.2.2 collection. That collection
also gives a standalone, serial server-upgrade playbook. At this time, neither path
coordinates a refresh of custom manifests across all servers.

A copy-and-patch task after `site` is not enough for these reasons:

- An interruption before that task can occur.
- An operator can use the upgrade playbook of the collection directly.

If one of these events occurs, the skipped components stay on old manifests.
The old manifests include old image and security settings.

Each server can regenerate the manifests independently at its start. This is also not enough during a
rolling upgrade. Old and new servers can publish different custom content.
K3s does not synchronize custom AddOn manifests between servers.

A new distributed refresh mechanism is a lifecycle change. It requires its own
upgrade, failure, and ownership-transfer tests. It is not a resource-only patch.

## Required Follow-up

1. Make one supported, coordinated K3s upgrade entry point.
   - Include recovery from an interrupted upgrade.
   - Make success conditional on the packaged-add-on refresh.
   - Detect alternate upgrades. Do not silently keep stale manifests.
2. Read the packaged originals from the installed K3s version on all actual servers.
   - Before publication, verify the expected versions and identical content.
   - Derive the custom manifests. Change only the named container `resources`.
   - Keep the upstream images, RBAC, configuration, and all other fields.
3. Stage identical derived files on all three servers.
   - Use unique AddOn basenames and non-deleting `.skip` markers.
   - Validate the ownership transfer and the rollback in a test cluster before production.
   - Never disable the live packaged AddOns.
4. Test these conditions:
   - Normal and interrupted rolling upgrades
   - Mixed server versions
   - Upstream container/schema changes
   - Unavailable servers
   - Upgrades that bypass the wrapper

   Alert on stale derived versions or unsupported manifest changes.

Until that lifecycle contract exists, K3s keeps the ownership. Thus, the upstream
security updates continue. The existing memory limit of CoreDNS stays in effect.
metrics-server and local-path-provisioner are explicit remaining limit gaps.
