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

The main Helm task declares the measured CPU and memory budgets for the nine
Mailu application components, with headroom for scanning and ClamAV signature
reloads. Edit those literals when the measured requirements change. Redis
keeps its upstream resource preset. MariaDB and PostgreSQL remain disabled.

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
hooks against the image tags rendered by the pinned `chart_versions.mailu`;
the chart version alone does not verify its daemon process layout.

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
