"""Check connection decisions and safety without running Ansible or kubectl."""

import os
from pathlib import Path
import unittest

from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment
import yaml


ROOT = Path(__file__).resolve().parents[3]
PLAYBOOKS = ROOT / "playbooks"
ENV = NativeEnvironment(undefined=StrictUndefined)
ENV.filters.update(expanduser=os.path.expanduser, dirname=os.path.dirname)


def load(path):
    return yaml.safe_load((PLAYBOOKS / path).read_text())


def evaluate(expression, **values):
    return ENV.compile_expression(expression, undefined_to_none=False)(**values)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.play = load("99-utils/kube-connect.yml")[0]
        self.tasks = self.play["tasks"]
        self.steps = [step for task in self.tasks for step in task.get("block", [task])]
        self.probe = next(t for t in self.steps if t.get("register") == "connection_probe")
        self.configure = next(t for t in self.tasks if "block" in t and
                              any("ansible.builtin.slurp" in s for s in t["block"]))
        self.verify = next(t for t in self.tasks if "block" in t and
                           any(s.get("register") == "verified_server" for s in t["block"]))
        self.values = {"kubeconfig_path": "/tmp/kube-connect-fixture/alternate-config",
                       "expected_server_url": "https://api.example.invalid:6443"}
        for name in ("kubectl_argv", "server_query", "allowed_server_urls"):
            self.values[name] = [ENV.from_string(item).render(**self.values)
                                 for item in self.play["vars"][name]]

    def test_only_allowed_current_contexts_are_probed_and_reused(self):
        for result, wanted in (({"rc": 0, "stdout": self.values["expected_server_url"]}, True),
                               ({"rc": 0, "stdout": "https://127.0.0.1:6443"}, True),
                               ({"rc": 0, "stdout": "https://other.invalid"}, False),
                               ({"rc": 1}, False)):
            with self.subTest(server=result):
                self.assertEqual(evaluate(self.probe["when"], server_url=result, **self.values), wanted)
        for result, wanted in (({}, True), ({"skipped": True}, True),
                               ({"rc": 1}, True), ({"rc": 0}, False)):
            with self.subTest(probe=result):
                self.assertEqual(evaluate(self.configure["when"], connection_probe=result), wanted)
        skip = next(t for t in self.tasks if "ansible.builtin.meta" in t)
        self.assertEqual(skip["ansible.builtin.meta"], "end_play")
        for facts, wanted in (({}, False), ({"kube_connected": False}, False),
                              ({"kube_connected": True}, True)):
            self.assertEqual(evaluate(skip["when"], hostvars={"localhost": facts}), wanted)

    def test_conflicting_paths_and_contexts_fail_before_any_work_or_cached_skip(self):
        guard = self.tasks[0]
        conditions = guard["ansible.builtin.assert"]["that"]
        self.assertNotIn("when", guard)
        configured = self.play["vars"]["kubeconfig_path"]
        absolute = os.path.expanduser("~/.kube/config")
        cases = [({}, configured, True), ({}, absolute, True),
                 ({}, "/tmp/other-config", False), ({}, "relative-config", False)]
        for key in ("KUBECONFIG", "K8S_AUTH_KUBECONFIG"):
            cases += [({key: value}, configured, wanted) for value, wanted in (
                ("", True), (absolute, True), ("/tmp/other-config", False),
                (absolute + ":/tmp/other-config", False), ("relative-config", False),
                ("~/.kube/config", False))]
        cases += [({key: "other-context"}, configured, False)
                  for key in ("K8S_AUTH_CONTEXT", "HELM_KUBECONTEXT")]
        for environment, selected, wanted in cases:
            with self.subTest(environment=environment, selected=selected):
                lookup = lambda plugin, key: environment.get(key, "")
                self.assertEqual(all(evaluate(condition, lookup=lookup, kubeconfig_path=selected)
                                     for condition in conditions), wanted)

    def test_final_validation_fails_closed_before_success_is_cached(self):
        checks = self.verify["block"]
        url_check = next(t for t in checks if t.get("register") == "verified_server")
        connectivity = next(t for t in checks if "ansible.builtin.command" in t and t != url_check)
        cache = next(t for t in checks if "ansible.builtin.set_fact" in t)
        self.assertLess(checks.index(url_check), checks.index(connectivity))
        self.assertLess(checks.index(connectivity), checks.index(cache))
        for result, wanted in (({"rc": 0, "stdout": self.values["expected_server_url"]}, False),
                               ({"rc": 0, "stdout": "https://127.0.0.1:6443"}, False),
                               ({"rc": 0, "stdout": "https://other.invalid"}, True),
                               ({"rc": 1}, True)):
            with self.subTest(result=result):
                self.assertEqual(evaluate(url_check["failed_when"], verified_server=result,
                                          **self.values), wanted)
        self.assertEqual(ENV.from_string(connectivity["ansible.builtin.command"]["argv"]).render(
            **self.values), self.values["kubectl_argv"] + ["get", "nodes", "-o", "name"])
        self.assertEqual(cache["ansible.builtin.set_fact"], {"kube_connected": True})
        self.assertLess(self.tasks.index(self.probe), self.tasks.index(self.configure))
        self.assertLess(self.tasks.index(self.configure), self.tasks.index(self.verify))
        self.assertEqual([t.get("register") for t in self.steps if t.get("failed_when") is False],
                         ["server_url", "connection_probe"])
        self.assertFalse(any(t.get("ignore_errors") or "rescue" in t for t in self.tasks + self.steps))
        self.assertNotIn("failed_when", connectivity)
        self.assertEqual([t for t in self.steps if "kube_connected" in t.get("ansible.builtin.set_fact", {})],
                         [cache])

    def test_check_mode_never_rewrites_the_endpoint_or_caches_success(self):
        endpoint = self.configure["block"][-1]
        for check_mode in (True, False):
            for task in (endpoint, self.verify):
                self.assertEqual(evaluate(task["when"], ansible_check_mode=check_mode), not check_mode)
        for task in self.steps:
            if task.get("check_mode") is False:
                self.assertIn(task.get("register"), ("server_url", "connection_probe"))

    def test_fetched_credentials_are_secret_and_written_after_directory_creation(self):
        steps = self.configure["block"]
        fetch, mkdir, save, endpoint = [next(t for t in steps if "ansible.builtin." + module in t)
                                       for module in ("slurp", "file", "copy", "command")]
        self.assertEqual([steps.index(t) for t in (fetch, mkdir, save, endpoint)],
                         sorted(steps.index(t) for t in (fetch, mkdir, save, endpoint)))
        self.assertEqual(fetch["ansible.builtin.slurp"]["src"], "/etc/rancher/k3s/k3s.yaml")
        self.assertIs(fetch["become"], True)
        self.assertEqual(ENV.from_string(fetch["delegate_to"]).render(groups={"server": ["first", "second"]}),
                         "first")
        self.assertEqual(mkdir["ansible.builtin.file"]["state"], "directory")
        for configured in (self.values["kubeconfig_path"], "~/.kube/config"):
            with self.subTest(kubeconfig_path=configured):
                expanded = os.path.expanduser(configured)
                self.assertEqual(ENV.from_string(mkdir["ansible.builtin.file"]["path"]).render(
                    kubeconfig_path=configured), os.path.dirname(expanded))
                self.assertEqual(ENV.from_string(save["ansible.builtin.copy"]["dest"]).render(
                    kubeconfig_path=configured), expanded)
                self.assertEqual(ENV.from_string(self.play["vars"]["kubectl_argv"][2]).render(
                    kubeconfig_path=configured), expanded)
        self.assertEqual(save["ansible.builtin.copy"]["mode"], "0600")
        for task in (fetch, save):
            self.assertIs(task["no_log"], True)
            self.assertNotIn("failed_when", task)
        self.assertEqual(ENV.from_string(endpoint["ansible.builtin.command"]["argv"]).render(**self.values),
                         self.values["kubectl_argv"] + ["config", "set-cluster", "default",
                                                       "--server=" + self.values["expected_server_url"]])

    def test_every_kubectl_call_uses_the_same_file_and_queries_the_active_context(self):
        self.assertEqual(self.values["kubectl_argv"], ["kubectl", "--kubeconfig", self.values["kubeconfig_path"]])
        self.assertIn("--minify", self.values["server_query"])
        self.assertNotIn("--raw", self.values["server_query"])
        for task in self.steps:
            self.assertNotIn("ansible.builtin.shell", task)
            if "ansible.builtin.command" in task:
                argv = ENV.from_string(task["ansible.builtin.command"]["argv"]).render(**self.values)
                self.assertEqual(argv[:3], self.values["kubectl_argv"])
                if task.get("register") in ("server_url", "verified_server"):
                    self.assertEqual(argv[3:], self.values["server_query"])


if __name__ == "__main__":
    unittest.main()
