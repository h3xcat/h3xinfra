"""Exercise CRD reconciliation with local kubectl doubles, never a cluster."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


PLAYBOOK = Path(__file__).resolve().parents[1] / "standup.yml"
TASKS = yaml.safe_load(PLAYBOOK.read_text())[1]["tasks"]
RECONCILE = next(t for t in TASKS if t["name"] == "Reconcile Envoy Gateway CRDs")
ANSIBLE = shutil.which("ansible-playbook")

KUBECTL = r'''
import json
import os
from pathlib import Path
import stat
import sys

args = sys.argv[1:]
assert args[0] in ("diff", "apply"), args
assert args[1:5] == ["--server-side", "--force-conflicts",
                     "--field-manager=h3xinfra-gateway-crds", "-f"], args
assert len(args) == 6 and args[-1] != "-", args
path = Path(args[-1])
assert stat.S_IMODE(path.stat().st_mode) == 0o600
assert path.stat().st_size > 3 * 1024 * 1024
with open(os.environ["CRD_TEST_CALLS"], "a") as log:
    log.write(json.dumps({"command": args[0], "path": str(path)}) + "\n")
rc = int(os.environ["CRD_TEST_" + args[0].upper() + "_RC"])
if rc > 1:
    print("admission policy rejected the CRD bundle", file=sys.stderr)
sys.exit(rc)
'''


class CrdOwnershipTests(unittest.TestCase):
    def test_gateway_api_is_owned_by_k3s(self):
        render = next(t for t in TASKS if t["name"] == "Render Envoy Gateway CRD bundle")
        command = render["ansible.builtin.command"]["cmd"]
        self.assertIn("--set crds.gatewayAPI.enabled=false", command)
        self.assertIn("--set crds.envoyGateway.enabled=true", command)
        self.assertNotIn("crds.gatewayAPI.channel", command)
        wait = next(t for t in TASKS if t["name"] == "Wait for K3s-managed Gateway API CRDs")
        self.assertLess(TASKS.index(wait), TASKS.index(render))
        self.assertIn("--for=condition=Established", wait["ansible.builtin.command"]["argv"])
        self.assertIn("crd/httproutes.gateway.networking.k8s.io", wait["ansible.builtin.command"]["argv"])
        controller = next(t for t in TASKS if t["name"] == "Deploy Envoy Gateway controller via Helm (OCI)")
        self.assertTrue(controller["kubernetes.core.helm"]["skip_crds"])


@unittest.skipUnless(ANSIBLE, "ansible-playbook required for CRD behavior tests")
class CrdReconciliationTests(unittest.TestCase):
    def run_reconciliation(self, diff_rc, apply_rc=0):
        with tempfile.TemporaryDirectory(prefix="gateway-crd-test-") as directory:
            root = Path(directory)
            kubectl = root / "kubectl"
            kubectl.write_text(f"#!{sys.executable}\n" + KUBECTL)
            kubectl.chmod(0o700)
            calls = root / "calls.jsonl"
            config = root / "ansible.cfg"
            config.write_text("[defaults]\nretry_files_enabled = False\n")
            playbook = root / "test.yml"
            playbook.write_text(yaml.safe_dump([{
                "name": "Test CRD reconciliation locally",
                "hosts": "localhost",
                "gather_facts": False,
                "vars": {"gateway_crd_manifests": "# synthetic CRD data\n" * 170000},
                "tasks": [RECONCILE],
            }]))
            env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR="1",
                       ANSIBLE_STDOUT_CALLBACK="default", ANSIBLE_CALLBACKS_ENABLED="",
                       PATH=str(root) + os.pathsep + os.environ["PATH"],
                       CRD_TEST_CALLS=str(calls), CRD_TEST_DIFF_RC=str(diff_rc),
                       CRD_TEST_APPLY_RC=str(apply_rc))
            result = subprocess.run(
                [ANSIBLE, "-i", "localhost,", "-c", "local", "-e",
                 "ansible_python_interpreter=" + sys.executable, str(playbook)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
                cwd=root, timeout=60,
            )
            self.assertTrue(calls.exists(), result.stdout + result.stderr)
            commands = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual(len({c["path"] for c in commands}), 1)
            self.assertFalse(Path(commands[0]["path"]).exists(), "temporary bundle leaked")
            return result, [c["command"] for c in commands]

    def test_in_sync_skips_apply_and_reports_no_changes(self):
        result, commands = self.run_reconciliation(0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(commands, ["diff"])
        self.assertRegex(result.stdout, r"changed=0\s")

    def test_drift_applies_the_same_file(self):
        result, commands = self.run_reconciliation(1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(commands, ["diff", "apply"])
        self.assertRegex(result.stdout, r"changed=1\s")

    def test_diff_error_preserves_stderr_and_never_applies(self):
        result, commands = self.run_reconciliation(2)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, ["diff"])
        self.assertIn("admission policy rejected the CRD bundle", result.stdout)
        self.assertNotIn("Broken pipe", result.stdout)

    def test_failed_apply_still_cleans_up(self):
        result, commands = self.run_reconciliation(1, apply_rc=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, ["diff", "apply"])
        self.assertIn("admission policy rejected the CRD bundle", result.stdout)


if __name__ == "__main__":
    unittest.main()
