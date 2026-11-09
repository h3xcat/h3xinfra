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

## Resource Budgets

The main Helm task declares CPU and memory requests and limits for the nine
enabled Mailu application components. The default values come from seven days
of observed application usage. They also include more headroom for mail scanning
and ClamAV signature reloads. Redis keeps its existing upstream resource preset.
This change does not enable MariaDB or PostgreSQL.

To override individual values, set them in the `k8s` inventory. The other
defaults stay the same:

```yaml
mailu:
  resources:
    clamav:
      limits:
        memory: "5Gi"
```

The ordinary `standup.yml` is still the canonical full deployment. For a
resource-only maintenance update of an existing release, do these steps:

1. Compare the deployed chart version and the resource values of the release.
2. Use that same chart version.
3. Reuse the existing release values. Override only the nine `*.resources`
   blocks.
4. Examine a server-side dry run. Do not print manifests or secret values.
5. Reject changes to images, environment, storage, Services, Secrets, or
   unrelated workloads.

When their pod templates change, the single-replica Recreate workloads will
interrupt the service for a short time.

Download the pinned upstream chart separately. Then run the focused tests:

```bash
helm pull mailu/mailu --version 2.7.3 --untar --untardir /tmp/mailu-resource-chart
MAILU_CHART_PATH=/tmp/mailu-resource-chart/mailu \
  python3 -m unittest discover -s playbooks/10-mailu/tests -v
```

The render tests check every enabled regular and init container. They make sure
that the Redis budget and the database enablement stay the same. They also make
sure that only the nine resource blocks change. The tests do not connect to the
cluster or read live release values.

## Graceful Shutdown

All ten enabled workloads have a 120-second termination grace period. A focused
post-renderer adds `preStop` hooks for these components:

- admin
- Postfix
- Dovecot
- front
- webmail
- ClamAV.

This post-renderer does not enable the older, unrelated Service post-renderer.
Helm 4 uses the local `postrenderer/v1` plugin. On each deployment, the playbook
copies the four files of this plugin into the Helm plugin directory. Helm 3 uses
the same renderer executable directly.

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
hooks. The supported source layout is Mailu 2024.06.55 as used with chart 2.7.3.

A change to a controller template does not add hooks or a longer grace period
to Pods that already run. For the first rollout from the old two-second Pods, do
these steps:

1. Arrange a maintenance window.
2. Take verified backups.
3. Explicitly stop the writers of the old Pods.
4. Replace the old Pods.

Do not assume that an ordinary Helm rollout makes that initial shutdown safe.
Later normal Pod replacements use the new hooks.

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
