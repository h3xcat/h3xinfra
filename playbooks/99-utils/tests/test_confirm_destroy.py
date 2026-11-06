"""Teardown confirmation guard: coverage and local behavior.

Runs ansible-playbook against a throwaway inventory whose remote hosts are
unroutable (TEST-NET-1); the guard must decide without opening a connection.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


UTILS = Path(__file__).resolve().parents[1]
PLAYBOOKS = UTILS.parent
GUARD = UTILS / "confirm-destroy.yml"
PHRASE = "yes-destroy-data"


def first_import(path):
    plays = yaml.safe_load(path.read_text())
    return plays[0].get("import_playbook") if plays else None


class CoverageTests(unittest.TestCase):
    def test_every_teardown_imports_the_guard_first(self):
        teardowns = sorted(PLAYBOOKS.glob("*/teardown.yml"))
        self.assertTrue(teardowns)
        for path in teardowns:
            with self.subTest(playbook=str(path.relative_to(PLAYBOOKS))):
                self.assertEqual(first_import(path), "../99-utils/confirm-destroy.yml")

    def test_stack_teardown_imports_the_guard_unconditionally_first(self):
        plays = yaml.safe_load((PLAYBOOKS / "stack-teardown.yml").read_text())
        self.assertEqual(plays[0].get("import_playbook"), "99-utils/confirm-destroy.yml")
        self.assertNotIn("when", plays[0])


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible-playbook not installed")
class BehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="h3x-confirm-destroy-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        (work / "ansible.cfg").write_text("[defaults]\nhost_key_checking = False\n")
        (work / "hosts.yml").write_text(yaml.safe_dump({
            "all": {"hosts": {
                "localhost": {"ansible_connection": "local"},
                "k8s": {"ansible_connection": "local"},
            }},
            "k3s_cluster": {"hosts": {
                "node1.invalid": {"ansible_host": "192.0.2.1", "ansible_ssh_timeout": 1},
            }},
        }))
        (work / "playbook.yml").write_text(yaml.safe_dump([
            {"import_playbook": str(GUARD)},
            {"name": "Destructive stand-in", "hosts": "k8s", "gather_facts": False,
             "tasks": [{"ansible.builtin.debug": {"msg": "DESTRUCTIVE-PLAY-RAN"}}]},
        ]))

    def run_playbook(self, *args):
        work = Path(self.work.name)
        env = dict(os.environ, ANSIBLE_CONFIG=str(work / "ansible.cfg"),
                   ANSIBLE_NOCOLOR="1", ANSIBLE_FORCE_COLOR="0")
        return subprocess.run(
            ["ansible-playbook", "-i", str(work / "hosts.yml"), str(work / "playbook.yml"), *args],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)

    def assert_refused(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Refusing to run a destructive teardown", result.stdout)
        self.assertNotIn("DESTRUCTIVE-PLAY-RAN", result.stdout)
        self.assertNotIn("UNREACHABLE", result.stdout)

    def test_refuses_without_phrase(self):
        self.assert_refused(self.run_playbook())

    def test_refuses_wrong_or_boolean_phrase(self):
        for value in ("yes", "true", PHRASE.upper(), PHRASE + "-x"):
            with self.subTest(value=value):
                self.assert_refused(self.run_playbook("-e", f"confirm_destroy={value}"))

    def test_limit_cannot_skip_the_guard(self):
        for limit in ("k8s", "node1.invalid"):
            with self.subTest(limit=limit):
                self.assert_refused(self.run_playbook("--limit", limit))

    def test_runs_with_exact_phrase(self):
        result = self.run_playbook("-e", f"confirm_destroy={PHRASE}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("DESTRUCTIVE-PLAY-RAN", result.stdout)
        self.assertNotIn("UNREACHABLE", result.stdout)


if __name__ == "__main__":
    unittest.main()
