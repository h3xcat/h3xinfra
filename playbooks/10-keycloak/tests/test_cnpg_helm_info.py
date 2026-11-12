"""Exercise the installed Helm module with a fake binary, without cluster access."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible-playbook required")
class CnpgHelmInfo(unittest.TestCase):
    def test_release_lookup_uses_helm4_all_states_without_removed_flag(self):
        tasks = yaml.safe_load((ROOT / "tasks/cnpg-operator.yml").read_text())
        args = dict(next(task["kubernetes.core.helm_info"] for task in tasks
                         if "kubernetes.core.helm_info" in task))
        with tempfile.TemporaryDirectory(prefix="cnpg-helm-info-") as directory:
            root = Path(directory)
            calls = root / "helm-args.json"
            binary = root / "helm"
            binary.write_text(
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "if args == ['version']:\n"
                "    print('version.BuildInfo{Version:\"v4.3.1\", GitCommit:\"fixture\"}')\n"
                "    sys.exit(0)\n"
                f"pathlib.Path({str(calls)!r}).write_text(json.dumps(args))\n"
                "if not args or args[0] != 'list' or '--all' in args:\n"
                "    sys.exit('unsupported Helm 4 command')\n"
                "print('[]')\n"
            )
            binary.chmod(0o755)
            args["binary_path"] = str(binary)
            playbook = root / "check.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "gather_facts": False,
                "tasks": [
                    {"kubernetes.core.helm_info": args, "register": "release"},
                    {"ansible.builtin.assert": {"that": "release.status is not defined"}},
                ],
            }]))
            config = root / "ansible.cfg"
            config.write_text("[defaults]\ninterpreter_python=auto_silent\n")
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("ANSIBLE_", "K8S_AUTH_", "HELM_"))}
            if "ANSIBLE_COLLECTIONS_PATH" in os.environ:
                env["ANSIBLE_COLLECTIONS_PATH"] = os.environ["ANSIBLE_COLLECTIONS_PATH"]
            env.update(ANSIBLE_CONFIG=str(config), ANSIBLE_STDOUT_CALLBACK="default",
                       KUBECONFIG="/dev/null")
            result = subprocess.run(
                ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook),
                 "-e", f"ansible_python_interpreter={sys.executable}"],
                cwd=root, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(calls.read_text()),
                             ["list", "--output=yaml", "--filter", "cloudnative-pg"])


if __name__ == "__main__":
    unittest.main()
