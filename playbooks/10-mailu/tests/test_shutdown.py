import copy
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


renderer = module('renderer', ROOT / 'shutdown-post-renderer.py')
stop = module('stop', ROOT / 'graceful-stop.py')
resources = module('resources', ROOT / 'tests/test_resources.py')


def fixtures():
    return [{'kind': 'Deployment', 'metadata': {'name': 'release-' + name, 'labels': {
        'app.kubernetes.io/component': name, 'app.kubernetes.io/instance': 'release'}},
        'spec': {'template': {'spec': {'terminationGracePeriodSeconds': 120, 'containers': [{'name': name}]}}}}
        for name in sorted(renderer.PYTHON_COMPONENTS | {'clamav'})]


class ShutdownTests(unittest.TestCase):
    def test_all_ten_components_have_sufficient_grace(self):
        values = resources.helm_values()
        for name in resources.EXPECTED:
            self.assertEqual(values[name]['terminationGracePeriodSeconds'], 120)
        self.assertEqual(values['redis']['master']['terminationGracePeriodSeconds'], 120)

    def test_renderer_only_adds_six_prestop_hooks(self):
        before = fixtures() + [{'kind': 'Secret', 'data': {'test': 'fixture'}}, {'kind': 'Service', 'spec': {'type': 'ClusterIP'}}]
        after = renderer.render(copy.deepcopy(before))
        for obj in after[:6]:
            container = obj['spec']['template']['spec']['containers'][0]
            hook = container.pop('lifecycle')
            self.assertIn('command', hook['preStop']['exec'])
        self.assertEqual(before, after)

    def test_missing_duplicate_wrong_owner_or_existing_hooks_fail(self):
        for mode in ('missing', 'duplicate', 'owner', 'hook', 'grace'):
            docs = fixtures()
            if mode == 'missing': docs.pop()
            if mode == 'duplicate': docs.append(copy.deepcopy(docs[0]))
            if mode == 'owner': docs[0]['metadata']['name'] = 'other-owner'
            if mode == 'hook': docs[0]['spec']['template']['spec']['containers'][0]['lifecycle'] = {'preStop': {}}
            if mode == 'grace': docs[0]['spec']['template']['spec']['terminationGracePeriodSeconds'] = 2
            with self.subTest(mode=mode), self.assertRaises(AssertionError): renderer.render(docs)

    def test_no_signal_to_reused_pid(self):
        before = {2: {'parent': 1, 'start': 'old'}}
        after = {2: {'parent': 1, 'start': 'new'}}
        with mock.patch.object(stop, 'processes', side_effect=[before, after]), mock.patch.object(stop.os, 'kill') as kill:
            with self.assertRaises(AssertionError): stop.stop_group([2], stop.signal.SIGTERM, 100)
            kill.assert_not_called()

    def test_forced_child_exit_is_not_clean(self):
        before = {2: {'parent': 1, 'start': 'old', 'state': 'S'}}
        after = {2: {'parent': 1, 'start': 'old', 'state': 'Z', 'exit': 9}}
        with mock.patch.object(stop, 'processes', side_effect=[before, before, after]), mock.patch.object(stop.os, 'kill'), \
             mock.patch.object(stop.time, 'monotonic', return_value=0):
            with self.assertRaises(AssertionError): stop.stop_group([2], stop.signal.SIGTERM, 100)

    def test_descendant_tree_includes_all_workers(self):
        self.assertEqual(stop.descendants({2: {'parent': 1}, 3: {'parent': 2}, 4: {'parent': 3}, 5: {'parent': 1}}, [2]), {2, 3, 4})

    def test_scripts_parse_without_execution(self):
        compile((ROOT / 'graceful-stop.py').read_text(), 'graceful-stop.py', 'exec')
        self.assertEqual(yaml.safe_load((ROOT / 'plugin.yaml').read_text())['type'], 'postrenderer/v1')

    def test_clamav_shell_finds_both_daemons_and_rejects_unclean_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for pid, name in ((20, 'freshclam'), (21, 'clamd')):
                (root / str(pid)).mkdir()
                fields = [str(pid), '(' + name + ')', 'Z', '1'] + ['0'] * 48
                (root / str(pid) / 'stat').write_text(' '.join(fields) + '\n')
            script = (ROOT / 'graceful-stop-clamav.sh').read_text().replace('/proc', directory)
            wrapped = 'kill() { return 0; }; sleep() { return 99; };\n' + script
            result = subprocess.run(['/bin/sh', '-c', wrapped], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"writersStopped":true', result.stdout)
            fields[-1] = '9'
            (root / '21' / 'stat').write_text(' '.join(fields) + '\n')
            result = subprocess.run(['/bin/sh', '-c', wrapped], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_vanished_process_during_signal_is_already_stopped(self):
        before = {2: {'parent': 1, 'start': 'old', 'state': 'S'}}
        with mock.patch.object(stop, 'processes', side_effect=[before, before, {}]), \
             mock.patch.object(stop.os, 'kill', side_effect=ProcessLookupError), \
             mock.patch.object(stop.time, 'monotonic', return_value=0):
            self.assertEqual(stop.stop_group([2], stop.signal.SIGTERM, 100), 1)


if __name__ == '__main__':
    unittest.main()
