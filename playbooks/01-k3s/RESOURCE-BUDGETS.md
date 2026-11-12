# Packaged Add-on Resource Exceptions

K3s owns CoreDNS and metrics-server. Keep their
packaged manifests under K3s management: do not patch their Deployments,
edit the manifests, add `.skip` markers or pass `--disable` as a resource
budget fix. `coredns.yml` is the explicit exception: it changes the replica
count through the scale subresource and adds a separate PDB, while K3s
continues to own CoreDNS.

## Ownership and upgrade constraint

[K3s rewrites packaged manifests at each server start](https://docs.k3s.io/installation/packaged-components).
A `.skip` marker leaves the existing objects but stops subsequent packaged
updates. `--disable` uninstalls the component. Neither is a safe shortcut
for a resource-only change. The separate [local-storage retirement](LOCAL-STORAGE.md)
uninstalls an unused component after consumer and recovery checks; it does not
replace its manifests with a resource-patched copy.

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
2. Read originals from every actual server in the `server` inventory group,
   verify versions and identical content, and change only the named container
   resources. Preserve images, RBAC and configuration.
3. Stage identical derived files with unique AddOn basenames on all servers.
   Test the transfer and rollback before production. Use non-deleting
   `.skip` markers only as part of that lifecycle; do not disable live AddOns
   as a resource-budget workaround.
4. Test interrupted and mixed-version upgrades, unavailable servers,
   upstream schema/container changes and wrapper bypasses. Alert on stale
   derived versions or unsupported changes.

Until then, K3s retains ownership and packaged security updates. CoreDNS's
upstream memory limit remains; metrics-server is an explicit resource-limit gap.

| Component | Source policy | Deployment evidence |
| --- | --- | --- |
| CoreDNS | K3s-owned, upstream memory limit; separate scale/PDB only | Preserve packaged ownership during upgrades. |
| metrics-server | K3s-owned, resource-limit gap remains | A resource change needs the lifecycle above. |
| local-path-provisioner | `local-storage` is disabled in both inventories | Until the attended retirement is verified, an installed provisioner remains an unbounded workload. Source alone does not prove removal. |
