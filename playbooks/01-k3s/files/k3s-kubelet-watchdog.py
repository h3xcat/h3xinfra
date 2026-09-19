#!/usr/bin/python3
"""Probe local kubelet; recover only the original hung worker agent process."""

import argparse
import fcntl
import http.client
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time


STATE_DIR = Path('/var/lib/k3s-kubelet-watchdog')
METRICS_FILE = Path('/var/lib/node_exporter/textfile_collector/k3s_kubelet_watchdog.prom')
FAILURE_THRESHOLD = 3
STARTUP_GRACE = 180
RECOVERY_LIMIT = 2
RECOVERY_WINDOW = 3600
DUMP_GRACE = 20


def log(message):
    print(message, flush=True)


def service_status(unit):
    properties = ('ActiveState', 'SubState', 'MainPID', 'InvocationID',
                  'ExecMainStartTimestampMonotonic', 'Restart', 'KillMode')
    result = subprocess.run(
        ['systemctl', 'show', unit, '--no-pager',
         '--property=' + ','.join(properties)],
        check=True, capture_output=True, text=True, timeout=5,
    )
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if any(key not in values for key in properties):
        raise ValueError('incomplete systemd service properties')
    values['MainPID'] = int(values['MainPID'])
    values['ExecMainStartTimestampMonotonic'] = int(values['ExecMainStartTimestampMonotonic']) / 1_000_000
    return values


def running(status):
    return (status['ActiveState'] == 'active' and status['SubState'] == 'running'
            and status['MainPID'] > 1 and bool(status['InvocationID']))


def same_process(original, current):
    return (running(current) and current['MainPID'] == original['MainPID']
            and current['InvocationID'] == original['InvocationID'])


def recoverable(status):
    return running(status) and status['Restart'] == 'always' and status['KillMode'] == 'process'


def probe(port):
    # A wall-clock alarm also bounds a peer that drips HTTP headers or body.
    def timed_out(_signum, _frame):
        raise TimeoutError('local kubelet probe exceeded five seconds')

    old_handler = signal.signal(signal.SIGALRM, timed_out)
    connection = None
    try:
        signal.setitimer(signal.ITIMER_REAL, 5)
        if port == 10250:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=5, context=context)
        else:
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        connection.request('GET', '/healthz', headers={'Connection': 'close'})
        response = connection.getresponse()
        if port == 10250:
            return response.status in (200, 401, 403)
        return response.status == 200 and response.read(16).strip() == b'ok'
    except (OSError, http.client.HTTPException):
        return False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if connection is not None:
            connection.close()


def fresh_state():
    return {'version': 1, 'boot_id': '', 'invocation': '', 'failures': 0,
            'recoveries': [], 'recovery_total': 0, 'last_recovery': 0}


def load_state(path):
    if not path.exists():
        return fresh_state()
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or state.keys() != fresh_state().keys() or state['version'] != 1:
        raise ValueError('unrecognized watchdog state')
    if any(not isinstance(state[key], str) for key in ('boot_id', 'invocation')):
        raise ValueError('invalid watchdog process identity')
    if any(type(state[key]) is not int or state[key] < 0 for key in ('failures', 'recovery_total')):
        raise ValueError('invalid watchdog counter')
    if not isinstance(state['recoveries'], list):
        raise ValueError('invalid watchdog recovery history')
    for timestamp in [state['last_recovery'], *state['recoveries']]:
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError('invalid watchdog recovery timestamp')
    return state


def atomic_write(path, content, mode):
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_state(path, state):
    atomic_write(path, json.dumps(state) + '\n', 0o600)


def write_metrics(path, unit, mode, state, healthy, blocked, now):
    labels = ','.join(key + '=' + json.dumps(value) for key, value in (
        ('node', socket.gethostname().split('.')[0]), ('unit', unit), ('mode', mode)))
    values = {
        'last_check_timestamp_seconds': now,
        'healthy': healthy,
        'consecutive_failures': state['failures'],
        'recovery_total': state['recovery_total'],
        'last_recovery_timestamp_seconds': state['last_recovery'],
        'recovery_blocked': int(blocked),
    }
    lines = []
    for suffix, value in values.items():
        name = 'h3xinfra_kubelet_watchdog_' + suffix
        lines.append('# TYPE ' + name + (' counter' if suffix == 'recovery_total' else ' gauge'))
        lines.append(name + '{' + labels + '} ' + str(value))
    atomic_write(path, '\n'.join(lines) + '\n', 0o644)


def recover(unit, original, charge_budget):
    if unit != 'k3s-agent.service' or not recoverable(original):
        raise ValueError('recovery requires an active worker agent with Restart=always and KillMode=process')
    descriptor = os.pidfd_open(original['MainPID'])
    try:
        current = service_status(unit)
        if not same_process(original, current) or not recoverable(current):
            log('Agent changed before recovery; leaving it untouched')
            return False
        # Persist the budget before signaling, even if this checker then exits.
        charge_budget()
        current = service_status(unit)
        if not same_process(original, current) or not recoverable(current):
            return False
        log('Kubelet probes failed repeatedly; sending SIGQUIT to worker agent PID '
            + str(original['MainPID']) + ' for a journal goroutine dump')
        signal.pidfd_send_signal(descriptor, signal.SIGQUIT)
        waiter = select.poll()
        waiter.register(descriptor, select.POLLIN)
        if not waiter.poll(DUMP_GRACE * 1000):
            current = service_status(unit)
            if same_process(original, current) and recoverable(current):
                log('Original worker agent is still alive after the dump grace; sending SIGKILL')
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            else:
                log('Agent changed during the dump grace; leaving the replacement untouched')
        return True
    except ProcessLookupError:
        log('Original worker agent exited before a signal was delivered')
        return False
    finally:
        os.close(descriptor)


def check(unit, mode, state, state_path, now, monotonic_now, boot_id):
    status = service_status(unit)
    if state['boot_id'] != boot_id or state['invocation'] != status['InvocationID']:
        state['failures'] = 0
        state['boot_id'] = boot_id
        state['invocation'] = status['InvocationID']
    if not running(status):
        state['failures'] = 0
        return -1, False
    started = status['ExecMainStartTimestampMonotonic']
    if started <= 0 or started > monotonic_now:
        raise ValueError('invalid service startup timestamp; refusing recovery')
    if monotonic_now - started < STARTUP_GRACE:
        state['failures'] = 0
        return -1, False
    if probe(10248) or probe(10250):
        state['failures'] = 0
        return 1, False
    if not same_process(status, service_status(unit)):
        state['failures'] = 0
        return -1, False
    state['failures'] += 1
    log('Both kubelet probes failed; consecutive failures=' + str(state['failures']))
    if state['failures'] < FAILURE_THRESHOLD:
        return 0, False
    if unit != 'k3s-agent.service' or mode != 'recover':
        log('Observe-only service; recovery disabled')
        return 0, False
    state['recoveries'] = [stamp for stamp in state['recoveries'] if now - stamp < RECOVERY_WINDOW]
    if len(state['recoveries']) >= RECOVERY_LIMIT:
        log('Recovery blocked: hourly limit reached')
        return 0, True

    def charge_budget():
        state['recoveries'].append(now)
        state['recovery_total'] += 1
        state['last_recovery'] = now
        save_state(state_path, state)

    recover(unit, status, charge_budget)
    state['failures'] = 0
    return 0, False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', choices=('k3s.service', 'k3s-agent.service'), required=True)
    parser.add_argument('--mode', choices=('observe', 'recover'), default='observe')
    args = parser.parse_args()
    mode = args.mode if args.unit == 'k3s-agent.service' else 'observe'
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE_DIR / 'watchdog.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        state = fresh_state()
        now = time.time()
        healthy, blocked, result = -1, True, 1
        try:
            state_path = STATE_DIR / 'state.json'
            state = load_state(state_path)
            boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            healthy, blocked = check(args.unit, mode, state, state_path, now, time.monotonic(), boot_id)
            save_state(state_path, state)
            result = 0
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            healthy, blocked = 0, True
            log('Watchdog failed closed: ' + str(error))
        try:
            write_metrics(METRICS_FILE, args.unit, mode, state, healthy, blocked, now)
        except OSError as error:
            log('Cannot publish watchdog metrics: ' + str(error))
            return 1
        return result


if __name__ == '__main__':
    sys.exit(main())
