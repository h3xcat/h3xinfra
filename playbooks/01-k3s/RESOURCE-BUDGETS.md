# Packaged Add-on Resource Exceptions

K3s owns CoreDNS, metrics-server and local-path-provisioner. Keep their
packaged manifests under K3s management: do not patch their Deployments,
edit the manifests, add `.skip` markers or pass `--disable` as a resource
budget fix. `coredns.yml` is the explicit exception: it changes the replica
count through the scale subresource and adds a separate PDB, while K3s
continues to own CoreDNS.

## Ownership and upgrade constraint

[K3s rewrites packaged manifests at each server start](https://docs.k3s.io/installation/packaged-components).
A `.skip` marker leaves the existing objects but stops subsequent packaged
updates. `--disable` uninstalls the component. Neither is a safe shortcut
for this resource-only change.

`01-k3s/standup.yml` imports the pinned `k3s.orchestration.site` collection
playbook. Operators can also use the collection's standalone serial upgrade
playbook. Neither path coordinates custom-manifest refresh across servers.
A post-install patch can be skipped by interruption or by an alternate
upgrade path, freezing old images and security settings. During a rolling
upgrade, servers may regenerate different versions; K3s does not synchronize
custom AddOn manifests between them.

Changing ownership therefore needs a separate, tested upgrade lifecycle:

1. Provide one coordinated upgrade entry point, recover interrupted runs,
   require packaged-add-on refresh for success, and detect bypasses.
2. Read originals from every actual server, verify versions and identical
   content, and change only the named container resources. Preserve images,
   RBAC and configuration.
3. Stage identical derived files with unique AddOn basenames on all servers.
   Test the transfer and rollback before production. Use non-deleting
   `.skip` markers only as part of that lifecycle; never disable live AddOns.
4. Test interrupted and mixed-version upgrades, unavailable servers,
   upstream schema/container changes and wrapper bypasses. Alert on stale
   derived versions or unsupported changes.

Until then, K3s retains ownership and packaged security updates. CoreDNS's
upstream memory limit remains; metrics-server and local-path-provisioner
are explicit resource-limit gaps.

## Historical measurements — 2026-09-19

These measurements describe that date, not a fresh live check. The servers
were `h3xpi01`, `h3xpi02` and `h3xpi03`; the other Pis and x86 nodes were
agents. Any future manifest management must target the actual `server`
inventory group, not all Pis or the whole cluster. These were plain `Addon`
resources, not Helm releases, and each Deployment then had one available
replica. CoreDNS scaling is now managed separately by `coredns.yml`.

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
