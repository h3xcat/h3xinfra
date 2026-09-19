# Mailu Resource Budgets

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
