# MetalLB L2 deployment

This layer deploys the pinned MetalLB controller and L2 speaker. Both FRR
backends are disabled. Gateway, Mailu and Plex own their dedicated address
pools and advertisements; each pool disables automatic assignment and each
LoadBalancer Service names its pool. Removing the old `-post` chart from source
**does not uninstall its existing release or remove its default pool**.

The shared private Helm suite checks controller and speaker resource budgets.
The privileged namespace is still necessary for the speaker's host networking
and `NET_RAW`; disabling FRR does not make this a restricted-PSA workload.

## Migrate an existing FRR-K8s installation

This is an attended network change. Source/render tests cannot establish that
live BGP configuration is unused or that VIP failover works. Do not deploy this
change as part of a full-stack run before completing these checks.

1. Schedule a quiet window with out-of-band access and a previous working
   checkout. Confirm the deployed MetalLB version matches the reviewed pin and
   record the main release's current successful Helm revision. Preserve the
   current main/post values and relevant custom resources in a protected local
   recovery directory; do not publish their contents.
2. Inspect the current `BGPPeer`, `BGPAdvertisement`, `BFDProfile` and FRR-K8s
   custom resources and identify their owners. Proceed only when no BGP or
   custom FRR configuration depends on this installation. Repository absence
   is insufficient evidence. Preserve any configuration before deleting its
   CRD; a Helm rollback does not recover deleted custom-resource contents.
3. Check every LoadBalancer Service's allocation and pool annotation, including
   both address families. Confirm the old default pool has no assignments and
   no Service requests it. Record the dedicated Gateway, Mailu and Plex pool
   addresses and current reachability from the networks that use them.
4. Review a server dry run against the current release. With MetalLB 0.16.2,
   the expected main-release changes are removal of 17 FRR-K8s objects,
   removal of its speaker RBAC rule, and removal of `METALLB_BGP_TYPE=frr-k8s`
   from the controller and speaker. **Both workloads roll**. Reject unexpected
   address, selector, Service, resource-budget or L2 configuration changes.
5. Deploy only `03-metallb/standup.yml` using the production inventory. The
   speaker rolls one node at a time; VIPs can fail over during the rollout.
   Helm also removes these four templated FRR-K8s CRDs and their instances:
   `bgpsessionstates.frrk8s.metallb.io`, `frrconfigurations.frrk8s.metallb.io`,
   `frrk8sconfigurations.frrk8s.metallb.io` and
   `frrnodestates.frrk8s.metallb.io`. The nine MetalLB CRDs remain.
6. Verify controller/speaker readiness, unchanged Service allocations, all
   dedicated pools and L2 advertisements, and Gateway/Mailu/Plex reachability.
   Exercise IPv4 and IPv6 where configured. Confirm the FRR workloads,
   webhook and four FRR-K8s CRDs are gone. Stop and recover on a regression.
7. Recheck that the old default pool remains unused, then explicitly uninstall
   its release (substitute the configured prefix/namespace if different):

   ```bash
   helm uninstall h3xinfra-metallb-post -n metallb-system --wait --timeout 300s
   ```

   Verify only the old default pool and its advertisement disappeared and
   repeat the dedicated-pool, allocation and reachability checks. Until this
   uninstall succeeds, the old pool still exists despite the source cleanup.

## Recovery and teardown

If the main rollout fails, use the recorded successful main release revision
with `helm rollback`, wait for controller, speaker and FRR readiness, and
repeat the allocation and reachability checks. Restore reviewed custom
configuration from the protected backup after its CRDs return; Helm rollback
alone cannot restore custom resources that CRD deletion removed.

If the old post release was already uninstalled and must be restored, use its
chart from the recorded previous public checkout and the saved post values
with `helm upgrade --install`. Confirm its original pool and advertisement
identities and keep `autoAssign: false`. Do not use a full-stack deployment or
teardown as a recovery shortcut.

The confirmation-protected teardown removes the main release and namespace;
this disrupts all MetalLB-backed Services. Helm owns the main CRDs, so there
is no additional shell command deleting a partial CRD list. Do not run this
teardown to perform the migration above.
