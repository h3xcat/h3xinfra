# Retire the bundled local-path provisioner

Both inventories add `local-storage` to the `disable` list of K3s. This changes
only the bundled storage component. The `04-health` chart, the namespace PSA
labels, the explicit `GET /livez` grant and the anonymous health check stay in
place.

K3s [`--disable` uninstalls a packaged component](https://docs.k3s.io/installation/packaged-components#using-the---disable-flag)
and removes its manifest from the K3s server. It does more than stop future
updates. The pinned [K3s v1.37.2+k3s1 manifest](https://github.com/k3s-io/k3s/blob/v1.37.2%2Bk3s1/manifests/local-storage.yaml)
owns six objects:

| Kind | Name | Namespace |
| --- | --- | --- |
| ServiceAccount | `local-path-provisioner-service-account` | `kube-system` |
| ClusterRole | `local-path-provisioner-role` | cluster-wide |
| ClusterRoleBinding | `local-path-provisioner-bind` | cluster-wide |
| Deployment | `local-path-provisioner` | `kube-system` |
| StorageClass | `local-path` | cluster-wide |
| ConfigMap | `local-path-config` | `kube-system` |

The StorageClass uses `rancher.io/local-path`. The manifest marks it as the
default class. It waits for a consumer before it binds a volume. Its reclaim
policy is `Delete`. The manifest does not contain PVs or PVCs. If you disable
the component, this is not a data migration or a backup. The removal of its
controller interrupts provisioning and reclaim operations.

## Before deployment

Source checks cannot prove that the live component is unused. The audit
earlier found no local-path PVs. This result is not a check of a rollout
precondition. Before you change a control-plane node, do these steps:

1. Arrange an attended window, out-of-band access and a known checkout that
   works. Verify that the actual K3s version on each control-plane node matches
   the reviewed pin. Verify that the cluster has a healthy quorum. Record the
   current node/API health. Keep the current encryption, registry, OIDC and
   other `01-k3s` preconditions intact.
2. Check all StorageClasses, their provisioners, parameters and default
   annotations. Do not infer the default from repository values. Verify that
   the intended replacement is healthy. Also verify that it is the sole default
   after the local-path removal. A fresh cluster has no storage default until
   you deploy its storage layer. Do not create default-dependent claims before
   then.
3. Check each PV and PVC in each namespace. Include resources in the Pending,
   Released and Terminating states, and retained resources. Check these
   fields:

   - `storageClassName`
   - Provisioner annotations
   - Claim references
   - Node affinity
   - The local/hostPath paths that hold the data

   Identify the classes with the name `local-path`. Also identify custom
   classes that use `rancher.io/local-path`. Check workload claim templates,
   charts, operators and queued jobs for future claims. Include claims with an
   omitted/null `storageClassName`. These claims depend on the default. An
   explicit empty string opts out of dynamic default provisioning. A Pending
   WaitForFirstConsumer claim is still a consumer.
4. Continue only if there are zero dependent PV/PVC and zero future consumers.
   If any exist, stop. Plan their migration and restoration separately. Do not
   delete a PV/PVC to make this check pass. The `Delete` reclaim behavior can
   delete the data of the volume. Quiesce claim creators for the window. Thus,
   new default-dependent or local-path claims cannot make the check invalid.
5. Preserve these items in a protected recovery directory:

   - The current K3s server configuration.
   - The packaged manifest.
   - The ConfigMap.
   - The RBAC.
   - The StorageClasses/default annotations.
   - Any storage metadata that you found.

   Keep the current datastore and application/data recovery procedures. An API
   object export does not back up node-local files. Verify that there are no
   custom manifests, `.skip` files, other configuration drop-ins or flags that
   change ownership or override `disable`. Review the intended six-object
   removal against the actual installed component. Fix discrepancies before
   deployment.

## Serial rollout and verification

Before the storage rollout, complete the [apiserver configuration and staged
rollout](README.md#apiserver-configuration-and-staged-rollout). Use the same
complete production inventory. That procedure has three stages:

1. A check and drift review on all control-plane nodes.
2. Verification on the first control-plane node.
3. Verification on all control-plane nodes.

The full standup entry point includes that consolidated play. Do not use the
full standup entry point to bypass the deployment gate of that play. The
standalone apiserver play expects unchanged start times only if its live
configuration already matches. That expectation does not apply to the
intentional K3s orchestration restarts in the storage procedure below.

Use the full production inventory and the normal `01-k3s/standup.yml`
entry point with `--forks 1`. For this attended rollout, also limit each run
to **one control-plane node by its inventory hostname**. Use the inventory
order. Start with `groups['server'][0]`. Then do each joined control-plane
node. Do not construct a reduced inventory, skip preflights with tags, or run
the whole stack as a shortcut.
From the private repository, the command shape is:

```sh
ansible-playbook h3xinfra/playbooks/01-k3s/standup.yml \
  -i inventory/production/hosts.yml --forks 1 --limit SERVER_INVENTORY_HOSTNAME
```

Use the usual protected Vault input. From the public repository, omit the
`h3xinfra/` path prefix. The pinned
[`k3s.orchestration` 1.2.2 server role](https://github.com/k3s-io/k3s-ansible/blob/1.2.2/roles/k3s_server/tasks/main.yml)
restarts K3s on the selected control-plane node, even on a repeated run.
`--forks 1` alone does not give a readiness barrier for each control-plane
node. Its later server-count/TCP checks are weaker than a fresh local
API/quorum check. After each run, make sure that these conditions are true
before you continue:

- The restarted control-plane node passes its local `/readyz` check.
- All other control-plane nodes are ready.
- The quorum is healthy.

Also check the node and the affected workloads. If a check fails, stop.
Recover before you touch another control-plane node.

Set the same disabled component on each control-plane node. During the
transition, an unconverted control-plane node can still manage its packaged
manifest. Do not declare the removal complete from the result of the first
control-plane node. Agent nodes do not own this packaged K3s server
component. Thus, this change needs no rollout on the agent nodes. Do not
manually delete or edit the packaged manifest. Do not run a K3s uninstall
script.

After all control-plane nodes converge, verify that the local-storage AddOn no
longer reconciles the component. Also verify that the six packaged objects
above are absent. Check the PV/PVC consumers and the intended single default
StorageClass again. Verify these conditions:

- CoreDNS and metrics-server remain healthy.
- The API and storage workloads are ready.
- The retained health grant/PSA labels and the anonymous `/livez` check still
  work.

Before you resume claim creators, verify a reviewed disposable claim through
the intended storage class. Include its cleanup. Record the rollout evidence.
The source merge and the synthetic tests do not supply it.

## Recovery

If a regression occurs, stop the rollout. Keep the claim creators paused.
Restore the previous inventory/server configuration. To do this, remove only
`local-storage` from the new disable list. Use the same pinned K3s version and
the full inventory. Use the same procedure for one control-plane node at a
time, with `--forks 1` and the readiness/quorum gates. Converge each
control-plane node. When you enable the component again, K3s regenerates its
packaged manifest. Verify all six objects, the controller readiness, the
provisioner settings and the default annotations. Compare them with the
protected record. Do this before you admit claims. The restored `local-path`
class can create a second default again. For new claims without a class,
Kubernetes chooses the
[most recently created default class](https://kubernetes.io/docs/concepts/storage/storage-classes/#default-storageclass).
Thus, the recreated local-path class can take precedence. Before you resume
claim creators, verify the intended default.

If you enable the component again, K3s brings back the packaged objects. It
does not bring back custom edits, deleted claims, volumes or their data.
Restore any necessary custom configuration and storage from the reviewed
recovery material. Use the procedure of the responsible owner. Never remove
node storage directories, remove finalizers or delete PV/PVCs as a cleanup
shortcut. Keep the explicit health grant during the full recovery.
