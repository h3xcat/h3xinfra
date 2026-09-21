#!/usr/bin/env python3
"""Stop Mailu companions before the daemon whose exit terminates its wrapper."""
import json
import os
from pathlib import Path
import signal
import sys
import time


def processes():
    result = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            raw = path.read_text()
            fields = raw.rsplit(') ', 1)[1].split()
            result[int(path.parent.name)] = {
                'name': raw.split('(', 1)[1].rsplit(')', 1)[0], 'state': fields[0],
                'parent': int(fields[1]), 'start': fields[19], 'exit': int(fields[49])}
        except (FileNotFoundError, ProcessLookupError):
            continue
    return result


def descendants(table, roots):
    selected = set(roots)
    while True:
        expanded = selected | {pid for pid, info in table.items() if info['parent'] in selected}
        if expanded == selected:
            return selected
        selected = expanded


def stop_group(roots, signo, deadline, allowed=(0,)):
    table = processes()
    tracked = {pid: table[pid] for pid in descendants(table, roots)}
    for pid in roots:
        current = processes().get(pid)
        if current is not None:
            assert current['start'] == tracked[pid]['start'], 'process identity changed'
            try:
                os.kill(pid, signo)
            except ProcessLookupError:
                pass
    while time.monotonic() < deadline:
        current = processes()
        alive = []
        for pid, before in tracked.items():
            after = current.get(pid)
            if after is None:
                continue
            assert after['start'] == before['start'], 'process identity changed'
            if after['state'] == 'Z':
                assert after['exit'] in tuple(x << 8 for x in allowed), 'unclean daemon exit'
            else:
                alive.append(pid)
        if not alive:
            return len(tracked)
        time.sleep(.1)
    raise RuntimeError('graceful daemon shutdown timed out')


def shutdown(component, held=False):
    assert os.getpid() != 1 and component in ('admin', 'postfix', 'dovecot', 'front', 'webmail')
    table = processes()
    expected_init = 'nginx' if component == 'webmail' else 'python3'
    assert table[1]['name'] == expected_init, 'unexpected init process'
    if held:
        assert table[1]['state'] in ('T', 't'), 'maintenance init hold absent'
    deadline = time.monotonic() + 80
    total = 0
    companions = [pid for pid, item in table.items() if item['parent'] == 1 and item['name'] == 'python3']
    assert len(companions) == {'admin': 0, 'postfix': 2, 'dovecot': 1, 'front': 1, 'webmail': 0}[component], 'unexpected companions'
    if companions:
        total += stop_group(companions, signal.SIGINT if component == 'front' else signal.SIGTERM, deadline, (0, 143))
    if component in ('front', 'webmail'):
        child_name = 'dovecot' if component == 'front' else 'php-fpm83'
        children = [pid for pid, item in processes().items() if item['parent'] == 1 and item['name'] == child_name]
        assert len(children) == 1, 'missing/ambiguous companion daemon'
        total += stop_group(children, signal.SIGTERM if component == 'front' else signal.SIGQUIT, deadline)
    main_name = {'admin': 'gunicorn', 'postfix': 'master', 'dovecot': 'dovecot', 'front': 'nginx', 'webmail': 'nginx'}[component]
    current = processes()
    candidates = [pid for pid, item in current.items() if item['name'] == main_name
                  and (item['parent'] == 1 or component == 'postfix' or pid == 1)]
    if component == 'webmail':
        candidates = [1] if current[1]['name'] == 'nginx' else []
    assert len(candidates) == 1, 'missing/ambiguous main daemon'
    if candidates == [1]:
        # Nginx is init here; PHP writers have already exited. Kubelet ends nginx.
        assert component == 'webmail'
    else:
        total += stop_group(candidates, signal.SIGQUIT if component == 'front' else signal.SIGTERM, deadline)
    print(json.dumps({'component': component, 'writersStopped': True, 'processesExited': total}), flush=True)


if __name__ == '__main__':
    try:
        shutdown(sys.argv[1], '--held' in sys.argv[2:])
    except Exception as error:
        print('MAILU_GRACEFUL_STOP_FAILED: ' + (str(error) if isinstance(error, (AssertionError, RuntimeError)) else type(error).__name__), file=sys.stderr, flush=True)
        raise SystemExit(1)
