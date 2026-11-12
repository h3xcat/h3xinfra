# Retire the bundled local-path provisioner

Both inventories add `local-storage` to K3s's `disable` list. This changes only
the bundled storage component. The `04-health` chart, namespace PSA labels,
explicit `GET /livez` grant and anonymous health check stay in place.

K3s [`--disable` uninstalls a packaged component](https://docs.k3s.io/installation/packaged-components#using-the---disable-flag)
and removes its manifest from the server. It does not merely stop future
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

The StorageClass uses `rancher.io/local-path`, is marked default, waits for a
consumer before binding, and has reclaim policy `Delete`. The manifest does
not contain PVs or PVCs. Disabling it is not a data migration or backup, and
removing its controller interrupts provisioning and reclaim operations.

## Before deployment

Source validation cannot establish that the live component is unused. The
audit's earlier observation of no local-path PVs is not a rollout precondition
check. Before changing any server:

1. Arrange an attended window, out-of-band access and a known working checkout.
   Verify the actual K3s version on every server matches the reviewed pin and
   the cluster has healthy quorum. Record the current node/API health. Keep
   existing encryption, registry, OIDC and other `01-k3s` preconditions intact.
2. Inspect all StorageClasses, their provisioners, parameters and default
   annotations; do not infer the default from repository values. Confirm the
   intended replacement is healthy and is the sole default after local-path
   removal. A fresh cluster has no storage default until its storage layer is
   deployed. Do not create default-dependent claims before then.
3. Inspect every PV and PVC in every namespace, including Pending, Released,
   terminating and retained resources. Check `storageClassName`, provisioner
   annotations, claim references, node affinity and local/hostPath backing
   paths; identify custom classes using `rancher.io/local-path` as well as the
   name `local-path`. Inspect workload claim templates, charts, operators and
   queued jobs for future claims. Include omitted/null `storageClassName`
   (default-dependent); an explicit empty string opts out of dynamic default
   provisioning. A Pending WaitForFirstConsumer claim is still a consumer.
4. Proceed only with zero dependent PV/PVC and future consumers. If any exist,
   stop and plan their migration and restoration separately. Do not delete a
   PV/PVC to make this check pass: `Delete` reclaim behavior can remove its
   backing data. Quiesce claim creators for the window so the check cannot be
   invalidated by new default-dependent or local-path claims.
5. Preserve current server configuration, the packaged manifest, ConfigMap,
   RBAC, StorageClasses/default annotations and any discovered storage
   metadata in a protected recovery directory. Retain the existing datastore
   and application/data recovery procedures; an API object export does not
   back up node-local files. Verify there are no custom manifests, `.skip`
   files, other config drop-ins or flags that change ownership or override
   `disable`. Review the intended six-object removal against the actual
   installed component. Resolve discrepancies before deployment.

## Serial rollout and verification

Use the full production inventory and the normal `01-k3s/standup.yml`
entry point with `--forks 1`. For this attended rollout, also limit each
invocation to **one server by its inventory hostname**, in inventory order:
first `groups['server'][0]`, then each joined server. Do not construct a reduced
inventory, skip preflights with tags, or run the whole stack as a shortcut.
From the private repository, the command shape is:

```sh
ansible-playbook h3xinfra/playbooks/01-k3s/standup.yml \
  -i inventory/production/hosts.yml --forks 1 --limit SERVER_INVENTORY_HOSTNAME
```

Use the usual protected Vault input. From the public repository, omit the
`h3xinfra/` path prefix. The pinned
[`k3s.orchestration` 1.2.2 server role](https://github.com/k3s-io/k3s-ansible/blob/1.2.2/roles/k3s_server/tasks/main.yml)
restarts the selected server even on a repeated run. `--forks 1` alone does not provide a
per-server readiness barrier: its later server-count/TCP checks are weaker
than a fresh local API/quorum check. After each invocation, require the
restarted server's local `/readyz`, all remaining servers' readiness and
healthy quorum before continuing; check the node and affected workloads too.
Stop on a failed check and recover before touching another server.

Set the same disabled component on every server. An unconverted server can
still manage its packaged manifest during the transition; do not declare
removal complete from the first server's result. Agents do not own this
packaged server component, so this change needs no agent rollout. Do not
manually delete or edit the packaged manifest or run a K3s uninstall script.

After all servers converge, verify the local-storage AddOn no longer
reconciles the component and the six packaged objects above are absent.
Recheck PV/PVC consumers and the intended single default StorageClass. Confirm
CoreDNS and metrics-server remain healthy, the API and storage workloads are
ready, and the retained health grant/PSA labels and anonymous `/livez` check
still work. Verify a reviewed disposable claim through the intended storage
class, including its cleanup, before resuming claim creators. Record the
rollout evidence; source merge and synthetic tests do not supply it.

## Recovery

Stop the rollout and leave claim creators paused on a regression. Restore the
previous inventory/server configuration (remove only `local-storage` from the
new disable list) using the same pinned K3s version, full inventory and
one-server-at-a-time procedure with `--forks 1` and readiness/quorum gates.
Converge every server. K3s regenerates its packaged manifest when re-enabled;
verify all six objects, controller readiness, provisioner settings and default
annotations against the protected record before admitting claims. The restored
`local-path` class can create a second default again. Kubernetes chooses the
[most recently created default class](https://kubernetes.io/docs/concepts/storage/storage-classes/#default-storageclass)
for new unspecified claims, so the
recreated local-path class can take precedence; verify the intended default
before resuming claim creators.

Re-enabling restores packaged objects, not custom edits, deleted claims,
volumes or their data. Restore any required custom configuration and storage
from the reviewed recovery material through the responsible owner's procedure.
Never remove node storage directories, strip finalizers or delete PV/PVCs as a
cleanup shortcut. Preserve the explicit health grant throughout recovery.
