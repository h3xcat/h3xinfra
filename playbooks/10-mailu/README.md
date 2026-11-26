# Mailu Operations

## Load Balancer Services

The pre-chart publishes the mail ports on two LoadBalancer Services. The two
Services share one MetalLB address.

- `<fullname>-external` publishes SMTP (25) and submission (465). All sources
  can connect. The external-dns records come from this Service.
- `<fullname>-external-clients` publishes IMAPS (993), POP3S (995), and
  ManageSieve (4190). Its `loadBalancerSourceRanges` holds the RFC 1918
  ranges, `fc00::/7`, and `networkPolicy.allowedIPv6Networks`. The Cilium
  policy uses the same CIDRs on these ports. This Service has no NodePorts,
  because `loadBalancerSourceRanges` does not apply to NodePorts.

Helm 4 `--wait` does not wait for a LoadBalancer address. After the pre-chart
deploys, `standup.yml` waits until each of the two Services has an address.
If the two Services do not share one address, the play stops with an error.

To allow a network to connect to the client ports, add its CIDR to
`mailu.network_policy.allowed_ipv6_networks` in the inventory.

## Network Policies

With `mailu.network_policy.enabled`, the pre-chart adds two Cilium policies:

- `<fullname>-same-namespace` selects every pod in the namespace. Only pods
  in the same namespace can connect. Postfix relays without authentication
  for the pod CIDR. Thus, other pods must not reach Postfix or port 25 of
  front.
- `<fullname>-email-access-policy` adds these entry points of the front pod:
  - Ports 25 and 465 from all external sources.
  - Ports 993, 995 and 4190 from the client source ranges.
  - Port 465 from the cluster. This port is for submission with
    authentication, for example Keycloak realm mail.
  - The Envoy Gateway proxies to the web port of the HTTPRoute.

The Mailu chart's own `networkPolicy` stays disabled. Its front rule opens
all mail ports to every source. The Redis of the pre-chart has its own
Kubernetes NetworkPolicy. See [Redis](#redis). The pre-chart renders this
policy also when `mailu.network_policy.enabled` is false.

## Redis

The Bitnami Redis subchart of the Mailu chart uses the frozen
`bitnamilegacy/redis` image (8.2.1). Thus, `standup.yml` turns it off with
`redis.enabled: false`. The pre-chart runs Redis instead, from
`templates/redis.yaml`. `externalRedis` points Mailu at the Service
`<fullname>-redis` on port 6379. Rspamd and the admin sessions read the host
from `REDIS_ADDRESS`. This variable has no port. Thus, the port must stay
6379.

- Data: Rspamd statistics and history (db 0), admin rate limits (db 2) and
  admin web sessions (db 3). The quota db (1) is empty.
- Image: the official `docker.io/library/redis` image at
  `image_versions.mailu_redis`, pinned by digest. 8.2 is an extended release.
  A `renovate.json` rule keeps the updates on 8.2. The container starts
  `redis-server` directly. Thus, the modules that the image entrypoint loads
  stay off.
- Security: no password, because Mailu does not support Redis
  authentication. The NetworkPolicy admits only pods in the release namespace
  on 6379. The pod runs as the image's `redis` user (999) with a read-only
  root file system, no capabilities and no ServiceAccount token.
- Persistence: an append-only file with fsync every second, and no RDB
  snapshots. `files/redis.conf` sets this, the same as the Bitnami Redis. The
  data is on a 1Gi Longhorn volume `data-<fullname>-redis-0`. If you delete
  the StatefulSet, the volume stays. A namespace teardown deletes the volume.

### Restore or move Redis data

Redis loads the append-only file directory at start and ignores a
`dump.rdb` next to it. You can load an RDB file into an empty volume before
Redis starts. The RDB file can be a backup or a copy of another Redis.
`redis-cli --rdb <file>` makes such a copy. To load the file, write it as the
base file of a new append-only directory. If an `appendonlydir` exists, move
it away first. Give `--rdb` a file name. With `-` (standard output), the file
keeps a 40-byte end marker. Thus, `redis-check-aof` rejects it.

1. Scale the StatefulSet to 0, or create the claim
   `data-<fullname>-redis-0` before the first deployment.
2. Start a pod with the same image, user 999 and `fsGroup` 999 that mounts
   the claim at `/data`.
3. In that pod, create `/data/appendonlydir`.
4. Copy the RDB file to `/data/appendonlydir/appendonly.aof.1.base.rdb`.
5. Write `/data/appendonlydir/appendonly.aof.manifest` with the one line
   `file appendonly.aof.1.base.rdb seq 1 type b`.
6. Check the directory with
   `redis-check-aof /data/appendonlydir/appendonly.aof.manifest`.
7. Delete the pod.
8. Start Redis. To do this, scale the StatefulSet to 1 or run the playbook.
9. Compare `redis-cli INFO keyspace` with the source.

Rspamd reads `REDIS_ADDRESS` only at start. When the Redis host changes,
restart the Rspamd Deployment after the main chart deployment. The move from
the Bitnami Redis is an example of such a change. The admin Deployment
restarts by itself because its pod template changes.

## Sieve Extensions

CVE-2026-42007 affects Dovecot 2.3. It is a use-after-free in the Sieve
`editheader` extension. A mailbox user can trigger it with a personal
script. The Dovecot override in `standup.yml` removes `editheader` from the
personal scripts (ManageSieve and the Roundcube filters). It stays available
to global scripts, because Mailu's `sieve_before` script uses it to rewrite
`Delivered-To`. A ConfigMap change does not restart Dovecot. After you change
the override, restart Dovecot.

## Resource Budgets

The main Helm task declares the measured CPU and memory budgets for the nine
Mailu application components. The budgets have headroom for scans and ClamAV
signature reloads. Edit those literals when the measured requirements change. The
Redis budget is `redis.resources` in the pre-chart values. MariaDB and
PostgreSQL remain disabled.

The shared Helm suite renders the upstream chart at `chart_versions.mailu`.
It checks the requests and limits on each enabled regular and init container.
Run the local routing and shutdown tests with the pinned test dependencies:

```bash
python3 -m unittest discover -s playbooks/10-mailu/tests -v
```

`standup.yml` is the full deployment. For a resource-only maintenance update,
reuse the deployed chart version and values. Change only the resource blocks.
Review a server-side dry run. Do not print manifests or secrets. Reject
unrelated changes to images, the environment or storage. Also reject
unrelated Service or Secret changes. A change to a single-replica Recreate pod
template interrupts the component for a short time.

## Graceful Shutdown

All ten enabled workloads have a 120-second termination grace period. A focused
post-renderer adds `preStop` hooks for these components:

- admin
- Postfix
- Dovecot
- front
- webmail
- ClamAV.

Helm 4 uses the local `postrenderer/v1` plugin. On each deployment, the playbook
copies the four files of the plugin into the Helm plugin directory. Then it uses
the plugin.

The Mailu Python wrapper exits on SIGTERM. It does not forward the signal to its
child daemons. Thus, the hooks stop the companion processes before the main
daemon. The exit of the main daemon ends that wrapper. Each process stops as
follows:

- Gunicorn, Postfix, and Dovecot receive their orderly shutdown signals.
- The certificate watcher of front receives SIGINT. This signal runs its
  observer cleanup.
- PHP-FPM receives SIGQUIT before the Nginx of webmail exits.
- The two daemonized processes of ClamAV stop before the `tail` init process
  of ClamAV ends. That init process can use all of the rest of the grace
  period, even when the daemons exit cleanly.

Redis and Rspamd keep their native daemon shutdown paths.

The Python hooks have a total wait limit of 80 seconds. They check the process
identity and the observed child exit status. An unexpected process layout or
deadline makes the hook fail. But these failures **do not cancel Kubernetes Pod
termination**. When you change Mailu images or the process topology, review the
hooks. Compare them with the pinned image tags in `image_versions.mailu` and
`image_versions.mailu_clamav`. The chart version alone does not verify its
daemon process layout.

A Pod gets these hooks and grace periods only when a new Pod replaces it. A
restore of older Pods still needs verified backups. It also needs an explicit
writer shutdown before the replacement. An ordinary Helm rollout does not make
that transition safe.

The focused shutdown tests cover these items:

- Hook-only renders
- Scope checks
- PID reuse and races with processes that disappear
- Daemon discovery
- Rejection of observed unclean child exits.

These tests add to the application-specific backup and post-restart integrity
checks. They do not replace these checks.

Signal semantics: [Gunicorn](https://gunicorn.org/signals/),
[Postfix](https://www.postfix.org/master.8.html),
[Dovecot](https://doc.dovecot.org/main/core/admin/running.html),
[Nginx](https://nginx.org/en/docs/control.html), and
[ClamAV](https://docs.clamav.net/manual/Usage/Scanning.html).

## External Workloads

`mailu.external_workloads` is an optional inventory flag. The default is
`false`. Set it to `true` when another tool owns the Mailu workloads. For
example, Argo CD can deploy them from a Git repository.

With the flag, `standup.yml` skips these two tasks:

- `Create Mailu namespace`. The other tool must create the namespace and its
  Pod Security labels before you run the playbook.
- `Deploy Mailu via Helm`. The playbook does not deploy the main release
  `<prefix>-main`.

The playbook still deploys the pre-chart release `<prefix>-pre`. This release
contains these objects:

- The MetalLB address pool and its L2Advertisement.
- The two LoadBalancer Services and the external-dns record.
- The Certificate.
- The HTTPRoutes, the BackendTLSPolicy, and the OIDC SecurityPolicy with its
  Secret.
- Redis.
- The Cilium network policies.

The playbook also installs the Helm 4 shutdown plugin on the Ansible
controller. This plugin does not change the cluster. To keep the graceful
shutdown, the other tool must add the `preStop` hooks itself. See
[Graceful Shutdown](#graceful-shutdown).

The pre-chart release expects these items from the other tool:

- The front pod has the labels `app.kubernetes.io/name: mailu`,
  `app.kubernetes.io/component: front` and
  `app.kubernetes.io/instance: <prefix>-main`. The LoadBalancer Services and
  the Cilium policy select the front pod with these labels.
- The front Service is `<prefix>-main-front` with port 443. The HTTPRoutes
  send the browser traffic to this Service.
- The front pod mounts the TLS Secret `<prefix>-pre-certificate`.
- Mailu uses the pre-chart Redis. See [Redis](#redis).

`stack-standup.yml` skips this layer when the flag is `true`. At that point,
the namespace does not exist on a new cluster. Run `standup.yml` after the
other tool creates the namespace. Until then, the Mailu pods do not become
ready, because Redis and the TLS Secret do not exist.

`teardown.yml` removes only the pre-chart release. It does not remove the
main release or the namespace. Remove them with the other tool.

WARNING: Do not run `standup.yml` without the flag after the other tool
takes over the workloads. Helm then deploys the main release again over the
objects of the other tool. Helm adopts an object that still has the
`meta.helm.sh/*` annotations of that release. It does not stop with an error.
