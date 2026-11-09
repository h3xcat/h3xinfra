"""Run the kube-proxy preflight play against a local, synthetic inventory."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


PLAYBOOK = Path(__file__).resolve().parents[1] / "kube-proxy-preflight.yml"


def run(server_config, cilium=None, extra_server_args=""):
    inventory = {
        "all": {"hosts": {"k8s": {"ansible_connection": "local"}}},
        "server": {"hosts": {"server1": {"ansible_connection": "local",
                                         "extra_server_args": extra_server_args}},
                   "vars": {"server_config_yaml": server_config}},
    }
    if cilium is not None:
        inventory["all"]["hosts"]["k8s"]["cilium"] = cilium
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "hosts.yml"
        path.write_text(yaml.safe_dump(inventory))
        config = Path(directory) / "ansible.cfg"
        config.write_text("[defaults]\n")
        env = {**os.environ, "ANSIBLE_CONFIG": str(config), "ANSIBLE_NOCOLOR": "1",
               "ANSIBLE_LOCALHOST_WARNING": "0", "ANSIBLE_INVENTORY_UNPARSED_WARNING": "0"}
        result = subprocess.run(
            ["ansible-playbook", "-i", str(path), str(PLAYBOOK)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, timeout=120,
        )
    return result.returncode, result.stdout + result.stderr


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible-playbook is not installed")
class KubeProxyPreflightTests(unittest.TestCase):
    def test_kube_proxy_enabled_passes_without_an_api_address(self):
        self.assertEqual(run("disable-kube-proxy: false\n")[0], 0)
        self.assertEqual(run("cluster-cidr: 10.42.0.0/16\n")[0], 0)
        self.assertEqual(run("")[0], 0)

    def test_disabled_kube_proxy_without_api_address_fails(self):
        for cilium in (None, {"namespace": "kube-system"}, {"k8s_service_host": "  "}):
            with self.subTest(cilium=cilium):
                code, output = run("disable-kube-proxy: true\n", cilium)
                self.assertNotEqual(code, 0)
                self.assertIn("cilium.k8s_service_host", output)

    def test_disabled_kube_proxy_in_extra_server_args_fails(self):
        code, _ = run("", extra_server_args="--node-ip=192.0.2.1 --disable-kube-proxy")
        self.assertNotEqual(code, 0)

    def test_disabled_kube_proxy_with_api_address_passes(self):
        code, output = run("disable-kube-proxy: true\n", {"k8s_service_host": "192.0.2.10"})
        self.assertEqual(code, 0, output)


if __name__ == "__main__":
    unittest.main()
