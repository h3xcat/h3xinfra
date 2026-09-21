#!/usr/bin/env python3
"""Add only Mailu's missing shutdown hooks; leave all other objects untouched."""
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parent
PYTHON_COMPONENTS = {'admin', 'postfix', 'dovecot', 'front', 'webmail'}


def render(documents):
    seen = set()
    for obj in documents:
        if not obj or obj.get('kind') not in ('Deployment', 'StatefulSet'):
            continue
        metadata = obj['metadata']
        labels = metadata.get('labels', {})
        component = labels.get('app.kubernetes.io/component')
        release = labels.get('app.kubernetes.io/instance')
        if component not in PYTHON_COMPONENTS | {'clamav'}:
            continue
        assert release and metadata['name'] == release + '-' + component
        assert component not in seen, 'duplicate shutdown target'
        containers = obj['spec']['template']['spec']['containers']
        selected = [item for item in containers if item['name'] == component]
        assert len(selected) == 1, 'ambiguous shutdown container'
        container = selected[0]
        assert not container.get('lifecycle'), 'refusing to replace existing lifecycle'
        if component in PYTHON_COMPONENTS:
            command = ['python3', '-c', (ROOT / 'graceful-stop.py').read_text(), component]
        else:
            command = ['/bin/sh', '-c', (ROOT / 'graceful-stop-clamav.sh').read_text()]
        container['lifecycle'] = {'preStop': {'exec': {'command': command}}}
        assert obj['spec']['template']['spec']['terminationGracePeriodSeconds'] >= 120
        seen.add(component)
    assert seen == PYTHON_COMPONENTS | {'clamav'}, 'missing shutdown targets'
    return documents


if __name__ == '__main__':
    try:
        documents = render(list(yaml.safe_load_all(sys.stdin)))
        yaml.safe_dump_all(documents, sys.stdout, sort_keys=False)
    except Exception:
        print('Mailu shutdown post-rendering failed; manifest withheld', file=sys.stderr)
        raise SystemExit(1)
