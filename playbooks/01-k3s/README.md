# Local Kubelet Watchdog

`kubelet-watchdog.yml` installs a 30-second systemd timer on cluster nodes. The
timer starts five minutes after boot. The watchdog ignores inactive services. It
also ignores the first 180 seconds after an agent starts.

These responses clear the failure counter:

- A successful local `10248/healthz` response
- An HTTP 200/401/403 response from `10250/healthz`.

Each of the two probes has a wall-clock timeout of five seconds. On workers,
three consecutive failed checks start recovery. The watchdog always observes
control-plane services without recovery. This is also true when `--mode recover`
is supplied by accident.

The watchdog sends SIGQUIT only to the original K3s agent main process. This signal
causes Go to dump goroutines into the service journal. The watchdog then waits
up to 20 seconds. If necessary, it then sends SIGKILL to that same process. Linux pidfds and
repeated systemd invocation checks prevent signals to a replacement process.
K3s must have `Restart=always` and `KillMode=process`. Then systemd brings the
main process back. The watchdog never restarts the service. It never sends
signals to the containerd/container process group of the service.

The watchdog persists each recovery attempt before it sends a signal. The limit
is two attempts per hour, also across agent or node restarts. Invalid state or
errors from systemd inspection block recovery. A drop-in for the journal rate
limit of the unit sets a larger allowance for Go dumps. The installation reloads
systemd but does not restart K3s. The running process can keep the previous
journal allowance until its next start. `systemctl show` confirms the loaded
setting. It does not confirm the publication of the setting to journald. On
systemd 255, this result comes from its
[unit execution setup](https://github.com/systemd/systemd/blob/v255/src/core/unit.c#L5258-L5416)
and [journald context refresh](https://github.com/systemd/systemd/blob/v255/src/journal/journald-context.c#L443-L503).
Thus, the first recovery dump can still be rate-limited.

To deploy the watchdog independently, use the usual inventory and vault setup.
Run these commands from the repository root:

```sh
ansible-playbook playbooks/01-k3s/kubelet-watchdog.yml
python3 -m unittest discover -s playbooks/01-k3s/tests -v
```

For an observation-only rollout, set `kubelet_watchdog_mode: observe` in
inventory, or pass `-e kubelet_watchdog_mode=observe`. Workers use `recover` by
default. Control-plane nodes are forced to `observe`.

To examine the timer, the journal, and the state on a node, run these commands:

```sh
systemctl status k3s-kubelet-watchdog.timer
journalctl -u k3s-kubelet-watchdog.service -u k3s-agent.service --since -1h
cat /var/lib/k3s-kubelet-watchdog/state.json
```

The watchdog writes metrics atomically to
`/var/lib/node_exporter/textfile_collector/k3s_kubelet_watchdog.prom`. Node
exporter must mount that host directory and enable its textfile collector.
`healthy` is `1` for a passed check, `0` for failed probes, and `-1` when the
check is skipped. Use Prometheus for recovery notifications and blocked-recovery
alerts.

To disable recovery for maintenance, stop both the timer and any in-flight
check. Use this command:
`systemctl stop k3s-kubelet-watchdog.timer k3s-kubelet-watchdog.service`.

The watchdog never starts a stopped K3s service. Keep the state during
maintenance so that the recovery budget stays intact. This watchdog does not fence
storage. It also does not prove that API heartbeats and workload reconciliation
advance.
