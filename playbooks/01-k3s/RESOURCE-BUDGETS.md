# Packaged Add-on Resource Exceptions

K3s owns CoreDNS and metrics-server. Keep their packaged manifests under K3s
management. Do not use these changes as a fix for a resource budget:

- A patch to their Deployments.
- An edit to the manifests.
- A `.skip` marker.
- The `--disable` flag.

`coredns.yml` is the explicit exception. It changes the replica count through
the scale subresource. It also adds a separate PDB. K3s continues to own
CoreDNS.

## Ownership and upgrade constraint

[K3s rewrites packaged manifests at each server start](https://docs.k3s.io/installation/packaged-components).
A `.skip` marker keeps the current objects, but it stops later packaged
updates. `--disable` uninstalls the component. Neither is a safe shortcut
for a resource-only change. The separate [local-storage retirement](LOCAL-STORAGE.md)
uninstalls an unused component after consumer and recovery checks. It does
not replace the manifests of the component with a resource-patched copy.

`01-k3s/standup.yml` runs the roles of the pinned `k3s.orchestration`
collection in the order of its `site.yml` playbook. You can also use the
standalone serial upgrade playbook of the collection. Neither path
coordinates the refresh of custom manifests across the control-plane nodes.
An interruption or an alternate upgrade path can skip a post-install patch.
This freezes old images and security settings. In an upgrade that goes from
node to node, the control-plane nodes can regenerate different versions. K3s
does not synchronize custom AddOn manifests between them.

Thus, a change of ownership needs a separate, tested upgrade lifecycle:

1. Provide one coordinated upgrade entry point. Recover interrupted runs.
   Require a packaged-add-on refresh for success. Detect bypasses.
2. Read the originals from every actual host in the `server` inventory group.
   Verify the versions. Verify that the content is identical. Change only the
   named container resources. Keep the images, RBAC and configuration.
3. Stage identical derived files with unique AddOn basenames on all
   control-plane nodes. Test the transfer and the rollback before production.
   Use `.skip` markers that do not delete objects only as part of that
   lifecycle. Do not disable live AddOns as a resource-budget workaround.
4. Test interrupted and mixed-version upgrades, unavailable control-plane
   nodes, upstream schema/container changes and wrapper bypasses. Add an alert
   for stale derived versions or unsupported changes.

Until then, K3s keeps the ownership and the packaged security updates.
CoreDNS keeps its upstream memory limit. metrics-server is an explicit
resource-limit gap.

| Component | Source policy | Deployment evidence |
| --- | --- | --- |
| CoreDNS | K3s-owned, upstream memory limit; separate scale/PDB only | Keep the packaged ownership during upgrades. |
| metrics-server | K3s-owned, resource-limit gap remains | A resource change needs the lifecycle above. |
| local-path-provisioner | `local-storage` is disabled in both inventories | Until you verify the attended retirement, an installed provisioner remains an unbounded workload. The source alone does not prove the removal. |
