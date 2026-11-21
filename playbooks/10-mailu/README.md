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

To let a network connect to the client ports, add its CIDR to
`mailu.network_policy.allowed_ipv6_networks` in the inventory.

## Network Policies

With `mailu.network_policy.enabled`, the pre-chart adds two Cilium policies:

- `<fullname>-same-namespace` selects every pod in the namespace. Only pods
  in the same namespace can connect. Postfix relays without authentication
  for the pod CIDR, so other pods must not reach it, or front port 25.
- `<fullname>-email-access-policy` adds the entry points of the front pod:
  25 and 465 from all external sources, 993, 995 and 4190 from the client
  source ranges, 465 from the cluster (authenticated submission, for example
  Keycloak realm mail), and the Envoy Gateway proxies to the web port of the
  HTTPRoute.

The Mailu chart's own `networkPolicy` stays disabled: its front rule opens
all mail ports to every source. The Redis of the pre-chart has its own
Kubernetes NetworkPolicy (see [Redis](#redis)). It is rendered also when
`mailu.network_policy.enabled` is false.

## Redis

The Bitnami Redis subchart of the Mailu chart uses the frozen
`bitnamilegacy/redis` image (8.2.1), so `standup.yml` turns it off
(`redis.enabled: false`). The pre-chart runs Redis instead
(`templates/redis.yaml`), and `externalRedis` points Mailu at the Service
`<fullname>-redis` on port 6379. Rspamd and the admin sessions read the host
from `REDIS_ADDRESS`, which has no port, so the port must stay 6379.

- Data: Rspamd statistics and history (db 0), admin rate limits (db 2) and
  admin web sessions (db 3). The quota db (1) is empty.
- Image: the official `docker.io/library/redis` image at
  `image_versions.mailu_redis`, pinned by digest. 8.2 is an extended release.
  A `renovate.json` rule keeps the updates on 8.2. The container starts
  `redis-server` directly, so the modules that the image entrypoint loads
  stay off.
- Security: no password, because Mailu does not support Redis
  authentication. The NetworkPolicy admits only pods in the release namespace
  on 6379. The pod runs as the image's `redis` user (999) with a read-only
  root file system, no capabilities and no ServiceAccount token.
- Persistence: append-only file with fsync every second, no RDB snapshots
  (`files/redis.conf`, the same as the Bitnami Redis), on a 1Gi Longhorn
  volume `data-<fullname>-redis-0`. The StatefulSet keeps the volume when it
  is deleted. A namespace teardown deletes it.

### Restore or move Redis data

Redis loads the append-only file directory at start and ignores a
`dump.rdb` next to it. To load an RDB file (a backup, or a copy of another
Redis made with `redis-cli --rdb <file>`) into an empty volume before Redis
starts, write it as the base file of a new append-only directory (move an
existing `appendonlydir` away first). Give `--rdb` a file name: with `-`
(standard output) the file keeps a 40-byte end marker, and
`redis-check-aof` rejects it.

1. Scale the StatefulSet to 0, or create the claim
   `data-<fullname>-redis-0` before the first deployment.
2. Start a pod with the same image, user 999 and `fsGroup` 999 that mounts
   the claim at `/data`.
3. In that pod, create `/data/appendonlydir`, copy the RDB file to
   `/data/appendonlydir/appendonly.aof.1.base.rdb`, and write
   `/data/appendonlydir/appendonly.aof.manifest` with the one line
   `file appendonly.aof.1.base.rdb seq 1 type b`.
4. Check the directory with
   `redis-check-aof /data/appendonlydir/appendonly.aof.manifest`.
5. Delete the pod, then start Redis (scale to 1 or run the playbook).
   Compare `redis-cli INFO keyspace` with the source.

Rspamd reads `REDIS_ADDRESS` only at start. When the Redis host changes, for
example on the move from the Bitnami Redis, restart the Rspamd Deployment
after the main chart deploy. The admin Deployment restarts by itself because
its pod template changes.

## Sieve Extensions

Dovecot 2.3 is affected by CVE-2026-42007, a use-after-free in the Sieve
`editheader` extension that a mailbox user can trigger with a personal
script. The Dovecot override in `standup.yml` removes `editheader` from the
personal scripts (ManageSieve and the Roundcube filters). It stays available
to global scripts, because Mailu's `sieve_before` script uses it to rewrite
`Delivered-To`. A ConfigMap change does not restart Dovecot; restart it after
you change the override.

## Resource Budgets

The main Helm task declares the measured CPU and memory budgets for the nine
Mailu application components, with headroom for scanning and ClamAV signature
reloads. Edit those literals when the measured requirements change. The
Redis budget is `redis.resources` in the pre-chart values. MariaDB and
PostgreSQL remain disabled.

The shared Helm suite renders the upstream chart at `chart_versions.mailu`
and checks requests and limits on every enabled regular and init container.
Run the local routing and shutdown tests with the pinned test dependencies:

```bash
python3 -m unittest discover -s playbooks/10-mailu/tests -v
```

`standup.yml` is the full deployment. For a resource-only maintenance update,
reuse the deployed chart version and values and change only the resource
blocks. Review a server-side dry run without printing manifests or secrets;
reject unrelated image, environment, storage, Service or Secret changes.
Changes to single-replica Recreate pod templates briefly interrupt service.

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
copies its four files into the Helm plugin directory before using the plugin.

The Mailu Python wrapper exits on SIGTERM. It does not forward the signal to its
child daemons. Thus, the hooks stop the companion processes before the main
daemon. The exit of the main daemon ends that wrapper. Each process stops as
follows:

- Gunicorn, Postfix, and Dovecot receive their orderly shutdown signals.
- The certificate watcher of front receives SIGINT. This signal runs its
  observer cleanup.
- PHP-FPM receives SIGQUIT before the Nginx of webmail exits.
- The two daemonized processes of ClamAV stop before its `tail` init process is
  terminated. That init process can use all of the remaining grace period, even
  when the daemons exit cleanly.

Redis and Rspamd keep their native daemon shutdown paths.

The Python hooks have a total wait limit of 80 seconds. They check the process
identity and the observed child exit status. Unexpected process layouts or
deadlines fail the hook, but these failures **do not cancel Kubernetes Pod
termination**. When you change Mailu images or the process topology, review the
hooks against the image tags pinned in `image_versions.mailu` and
`image_versions.mailu_clamav`; the chart version alone does not verify its
daemon process layout.

Existing Pods gain these hooks and grace periods only when replaced. A restore
of older Pods still needs verified backups and an explicit writer shutdown
before replacement; an ordinary Helm rollout does not make that transition safe.

The focused shutdown tests cover these items:

- Hook-only rendering
- Scope checks
- PID reuse and disappearing-process races
- Daemon discovery
- Rejection of observed unclean child exits.

These tests add to the application-specific backup and post-restart integrity
checks. They do not replace these checks.

Signal semantics: [Gunicorn](https://gunicorn.org/signals/),
[Postfix](https://www.postfix.org/master.8.html),
[Dovecot](https://doc.dovecot.org/main/core/admin/running.html),
[Nginx](https://nginx.org/en/docs/control.html), and
[ClamAV](https://docs.clamav.net/manual/Usage/Scanning.html).
