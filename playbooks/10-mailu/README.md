# Mailu Operations

## Resource Budgets

The main Helm task declares CPU and memory requests and limits for the nine
enabled Mailu application components. Defaults were sized from seven days of
observed application usage, with additional headroom for mail scanning and
ClamAV signature reloads. Redis retains its existing upstream resource preset;
this change does not enable MariaDB or PostgreSQL.

Override individual values in the `k8s` inventory without replacing the other
defaults:

```yaml
mailu:
  resources:
    clamav:
      limits:
        memory: "5Gi"
```

The ordinary `standup.yml` remains the canonical full deployment. For an
existing release, first compare its deployed chart version and resource values.
A resource-only maintenance update should use that same chart version and reuse
the existing release values, overriding only the nine `*.resources` blocks.
Inspect a server-side dry run without printing manifests or secret values;
reject changes to images, environment, storage, Services, Secrets, or unrelated
workloads. The single-replica Recreate workloads will briefly interrupt service
when their pod templates change.

Run the focused tests with the pinned upstream chart downloaded separately:

```bash
helm pull mailu/mailu --version 2.7.3 --untar --untardir /tmp/mailu-resource-chart
MAILU_CHART_PATH=/tmp/mailu-resource-chart/mailu \
  python3 -m unittest discover -s playbooks/10-mailu/tests -v
```

The render tests check every enabled regular and init container, preserve the
Redis budget and database enablement, and verify that only the nine resource
blocks change. They do not contact the cluster or read live release values.

## Graceful Shutdown

All ten enabled workloads have a 120-second termination grace period. A focused
post-renderer adds `preStop` hooks for admin, Postfix, Dovecot, front, webmail,
and ClamAV. It does not enable the older, unrelated Service post-renderer.
Helm 4 uses the local `postrenderer/v1` plugin; the playbook copies its four
files into Helm's plugin directory on each deployment. Helm 3 uses the same
renderer executable directly.

Mailu's Python wrapper exits on SIGTERM without forwarding it to its child
daemons. The hooks therefore stop companions before the main daemon whose exit
ends that wrapper. Gunicorn, Postfix, and Dovecot receive their orderly shutdown
signals. Front's certificate watcher receives SIGINT, which runs its observer
cleanup. PHP-FPM receives SIGQUIT before webmail's Nginx exits. ClamAV's two
daemonized processes stop before its `tail` init process is terminated; that
init process may consume the remaining grace period despite clean daemon exits.
Redis and Rspamd retain their native daemon shutdown paths.

The Python hooks have an 80-second total wait bound and check process identity
and observed child exit status. Unexpected process layouts or deadlines fail
the hook, but **do not cancel Kubernetes Pod termination**. Review the hooks
when changing Mailu images or process topology. The supported source layout is
Mailu 2024.06.55 as used with chart 2.7.3.

Changing a controller template does not add hooks or a longer grace period to
already-running Pods. For the first rollout from the old two-second Pods,
arrange a maintenance window, take verified backups, and explicitly stop their
writers before replacing them. Do not assume an ordinary Helm rollout makes
that initial shutdown safe. Follow-up normal Pod replacements use the new hooks.

The focused shutdown tests cover hook-only rendering, scope checks, PID reuse
and disappearing-process races, daemon discovery, and rejection of observed
unclean child exits. They supplement, rather than replace, application-specific
backup and post-restart integrity checks.

Signal semantics: [Gunicorn](https://gunicorn.org/signals/),
[Postfix](https://www.postfix.org/master.8.html),
[Dovecot](https://doc.dovecot.org/main/core/admin/running.html),
[Nginx](https://nginx.org/en/docs/control.html), and
[ClamAV](https://docs.clamav.net/manual/Usage/Scanning.html).
