# Local Kubelet Watchdog

`kubelet-watchdog.yml` installs a 30-second systemd timer on cluster nodes. It
starts five minutes after boot and ignores inactive services or the first
180 seconds after an agent starts. A successful local `10248/healthz` response
or an HTTP 200/401/403 response from `10250/healthz` clears the failure counter.
Both probes have a five-second wall-clock timeout. Three consecutive failed
checks trigger recovery on workers; control-plane services are always observed
without recovery, including when `--mode recover` is accidentally supplied.

The watchdog sends SIGQUIT only to the original K3s agent main process so Go
dumps goroutines into the service journal. It waits up to 20 seconds before
sending SIGKILL to that same process if needed. Linux pidfds and repeated
systemd invocation checks prevent signaling a replacement. K3s must have
`Restart=always` and `KillMode=process`; systemd brings the main process back.
The watchdog never restarts the service or signals its containerd/container
process group.

Recovery attempts are persisted before signaling and limited to two per hour,
including across agent or node restarts. Invalid state or systemd inspection
errors block recovery. A unit journal rate-limit drop-in configures a larger
allowance for Go dumps. Installation reloads systemd without restarting K3s;
the running process may retain the previous journal allowance until its next
start. `systemctl show` confirms the loaded setting, not its publication to
journald. On systemd 255, this follows from its
[unit execution setup](https://github.com/systemd/systemd/blob/v255/src/core/unit.c#L5258-L5416)
and [journald context refresh](https://github.com/systemd/systemd/blob/v255/src/journal/journald-context.c#L443-L503).
The first recovery dump can therefore still be rate-limited.

Deploy independently from the repository root, using the usual inventory and
vault setup:

```sh
ansible-playbook playbooks/01-k3s/kubelet-watchdog.yml
python3 -m unittest discover -s playbooks/01-k3s/tests -v
```

Set `kubelet_watchdog_mode: observe` in inventory or pass
`-e kubelet_watchdog_mode=observe` for an observation-only rollout. Workers
default to `recover`; control-plane nodes are forced to `observe`.

On a node, inspect the timer, journal and state with:

```sh
systemctl status k3s-kubelet-watchdog.timer
journalctl -u k3s-kubelet-watchdog.service -u k3s-agent.service --since -1h
cat /var/lib/k3s-kubelet-watchdog/state.json
```

Metrics are atomically written to
`/var/lib/node_exporter/textfile_collector/k3s_kubelet_watchdog.prom`; node
exporter must mount that host directory and enable its textfile collector.
`healthy` is `1` for a passed check, `0` for failed probes, and `-1` when skipped.
Recovery notifications and blocked-recovery alerts belong in Prometheus.
Disable recovery for maintenance by stopping both the timer and any in-flight
check with `systemctl stop k3s-kubelet-watchdog.timer k3s-kubelet-watchdog.service`.
A stopped K3s service is never started by the watchdog. Retain state during
maintenance so the recovery budget remains intact. This watchdog does not
fence storage or prove that API heartbeats and workload reconciliation are
advancing.
