# Packaged Add-on Resource Exceptions

CoreDNS, metrics-server, and local-path-provisioner remain owned by K3s. This
change deliberately does not patch their Deployments, edit packaged manifests,
create `.skip` markers, or pass `--disable` for these components.

## Verified State

The production inventory and live control-plane labels agree: the K3s servers
are **h3xpi01, h3xpi02, and h3xpi03**. The other two Pis and all three x86 nodes
are agents. Server-manifest management must target the `server` inventory group,
not all Pis or the entire cluster.

These are plain `Addon` resources, not `HelmChart` releases with resource-value
overrides. As observed on 2026-09-19, each Deployment has one available replica:

| Container | Existing requests | Existing limits | Proposed requests | Proposed limits |
| --- | --- | --- | --- | --- |
| CoreDNS | 100m CPU, 70Mi memory | 170Mi memory | 100m CPU, 128Mi memory | 1 CPU, 512Mi memory |
| metrics-server | 100m CPU, 70Mi memory | None | 100m CPU, 128Mi memory | 1 CPU, 512Mi memory |
| local-path-provisioner | None | None | 25m CPU, 64Mi memory | 500m CPU, 256Mi memory |

The proposed ephemeral-storage request/limit is 32Mi/256Mi for each container.
These are future targets, **not deployed budgets**. Seven-day memory working-set
maxima were approximately 83Mi, 94Mi, and 47Mi respectively; CPU-rate maxima were
9m, 16m, and 0.4m. No init containers were present in these Deployments.

## Why Ownership Has Not Changed

[K3s packaged-component documentation](https://docs.k3s.io/installation/packaged-components)
states that packaged manifests are rewritten at each server start. A `.skip`
marker retains existing objects but prevents subsequent packaged updates from
being applied. `--disable` actively uninstalls the component and is not an
appropriate ownership-transfer mechanism for this resource-only maintenance.

The repository's `bin/h3xinfra-deploy-stack` imports `01-k3s/standup.yml`, which
imports `k3s.orchestration.site` from the pinned 1.2.2 collection. That collection
also provides a standalone, serial server-upgrade playbook. Neither path
currently coordinates a custom-manifest refresh across every server.

A copy-and-patch task after `site` is insufficient: interruption before that
task, or use of the collection's upgrade playbook directly, would leave skipped
components on old manifests, including old image and security settings.
Independently regenerating on each server start is also insufficient during a
rolling upgrade: old and new servers could publish different custom content.
K3s does not synchronize custom AddOn manifests between servers. Introducing a
new distributed refresh mechanism is a lifecycle change requiring its own
upgrade, failure, and ownership-transfer tests, not a resource-only patch.

## Required Follow-up

1. Establish one supported, coordinated K3s upgrade entry point, including
   interrupted-upgrade recovery. It must gate success on packaged-add-on refresh
   and detect alternate upgrades instead of silently retaining stale manifests.
2. Read packaged originals from the installed K3s version on all actual servers;
   verify expected versions and identical content before publication. Derive
   custom manifests by changing only named container `resources`, preserving
   upstream images, RBAC, configuration, and all other fields.
3. Stage identical derived files with unique AddOn basenames and non-deleting
   `.skip` markers on all three servers. Validate ownership transfer and rollback
   in a test cluster before production. Never disable the live packaged AddOns.
4. Test normal and interrupted rolling upgrades, mixed server versions, upstream
   container/schema changes, unavailable servers, and upgrades that bypass the
   wrapper. Alert on stale derived versions or unsupported manifest changes.

Until that lifecycle contract exists, preserving K3s ownership keeps upstream
security updates flowing. CoreDNS's existing memory limit remains in effect;
metrics-server and local-path-provisioner are explicit remaining limit gaps.
