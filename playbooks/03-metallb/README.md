# MetalLB L2 deployment

This layer deploys the pinned MetalLB controller and L2 speaker. Both FRR
backends are disabled. Gateway, Mailu and Plex own their dedicated address
pools and advertisements. Each pool disables automatic assignment. Each
LoadBalancer Service names its pool. If you remove the old `-post` chart from
the source, **this does not uninstall its current release or remove its default
pool**.

The shared private Helm suite checks the resource budgets of the controller
and the speaker. The speaker still needs the privileged namespace for its host
networking and `NET_RAW`. FRR is disabled, but this does not make the speaker a
restricted-PSA workload.

## Migrate an existing FRR-K8s installation

This is an attended network change. Source/render tests cannot prove that the
live BGP configuration is unused. They also cannot prove that VIP failover
works. Do not deploy this change as part of a full-stack run before you
complete these checks.

1. Schedule a quiet window. Have out-of-band access and a previous checkout
   that works. Verify that the deployed MetalLB version matches the reviewed
   pin. Record the current successful Helm revision of the main release. Keep a
   copy of the current main/post values and the relevant custom resources in a
   protected local recovery directory. Do not publish their contents.
2. Check the current `BGPPeer`, `BGPAdvertisement`, `BFDProfile` and FRR-K8s
   custom resources. Identify their owners. Continue only if no BGP or custom
   FRR configuration depends on this installation. The absence of a
   configuration from the repository is not sufficient evidence. Before you
   delete a CRD, keep a copy of its configuration. A Helm rollback does not
   restore the contents of deleted custom resources.
3. Check the allocation and the pool annotation of each LoadBalancer Service
   for both address families. Verify that the old default pool has no
   assignments. Verify that no Service requests it. Record the addresses of the
   dedicated Gateway, Mailu and Plex pools. Also record their current
   reachability from the networks that use them.
4. Review a server dry run against the current release. With MetalLB 0.16.2,
   expect these changes to the main release:

   - Removal of 17 FRR-K8s objects.
   - Removal of its speaker RBAC rule.
   - Removal of `METALLB_BGP_TYPE=frr-k8s` from the controller and the speaker.

   **Both workloads roll**. Reject an unexpected change to an address, a
   selector, a Service, a resource budget or the L2 configuration.
5. Deploy only `03-metallb/standup.yml` with the production inventory. The
   speaker rolls one node at a time. VIPs can fail over during the rollout.
   Helm also removes these four templated FRR-K8s CRDs and their instances:
   `bgpsessionstates.frrk8s.metallb.io`, `frrconfigurations.frrk8s.metallb.io`,
   `frrk8sconfigurations.frrk8s.metallb.io` and
   `frrnodestates.frrk8s.metallb.io`. The nine MetalLB CRDs remain.
6. Verify these items:

   - The controller and the speaker are ready.
   - The Service allocations did not change.
   - All dedicated pools and L2 advertisements are present.
   - Gateway, Mailu and Plex are reachable.

   Test each configured address family: IPv4 and IPv6. Verify that the FRR
   workloads, the webhook and the four FRR-K8s CRDs are gone. If a regression
   occurs, stop. Then recover. See
   [Recovery and teardown](#recovery-and-teardown).
7. Check again that the old default pool is unused. Then uninstall its
   release explicitly. If the configured prefix or namespace is different, use
   the configured value in the command:

   ```bash
   helm uninstall h3xinfra-metallb-post -n metallb-system --wait --timeout 300s
   ```

   Verify that only the old default pool and its advertisement disappeared.
   Then do the dedicated-pool, allocation and reachability checks again. The
   source cleanup does not remove the old pool. The pool exists until this
   uninstall succeeds.

## Recovery and teardown

If the main rollout fails, run `helm rollback` with the recorded successful
revision of the main release. Wait until the controller, the speaker and FRR
are ready. Then do the allocation and reachability checks again. After the
CRDs return, restore the reviewed custom configuration from the protected
backup. A Helm rollback alone cannot restore custom resources that the CRD
deletion removed.

If you uninstalled the old post release and you must restore it, run
`helm upgrade --install`. Use its chart from the recorded previous public
checkout and the saved post values. Verify the original identities of its pool
and its advertisement. Keep `autoAssign: false`. Do not use a full-stack
deployment or teardown as a recovery shortcut.

The confirmation-protected teardown removes the main release and the
namespace. This disrupts all Services that use MetalLB. Helm owns the main
CRDs. Thus, no additional shell command deletes a partial CRD list. Do not run
this teardown to do the migration above.
