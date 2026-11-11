"""Static checks for registries.yml (containerd registry configuration).

The play vars are rendered with plain Jinja2 plus small stand-ins for the
Ansible filters and tests that they use. No hosts are contacted.
"""

from pathlib import Path
import re
import time
import unittest

import jinja2
from jinja2.nativetypes import NativeEnvironment
import yaml


LAYER = Path(__file__).resolve().parents[1]
PLAYBOOK = LAYER / "registries.yml"
TASKS = LAYER / "tasks" / "registries.yml"
GROUPS = {"server": ["s1", "s2", "s3"], "agent": ["a1"], "k3s_cluster": ["s1", "s2", "s3", "a1"]}
SECRET = "not-a-real-token-0123456789"
GHCR = {"configs": {"ghcr.io": {"auth": {"username": "example", "password": SECRET}}}}


def load(path):
    return yaml.safe_load(path.read_text())


def to_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(value)


def environment(native=True):
    env = (NativeEnvironment if native else jinja2.Environment)(undefined=jinja2.ChainableUndefined)
    env.filters.update({
        "bool": to_bool,
        "dict2items": lambda d: [{"key": k, "value": v} for k, v in d.items()],
        "items2dict": lambda items: {i["key"]: i["value"] for i in items},
        "to_nice_yaml": lambda data, indent=4: yaml.safe_dump(data, indent=indent, default_flow_style=False),
        "dirname": lambda p: str(Path(p).parent),
        "strftime": lambda fmt, seconds, utc=False: time.strftime(fmt, (time.gmtime if utc else time.localtime)(seconds)),
    })
    env.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
    return env


class RegistriesPlaybook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plays = load(PLAYBOOK)
        cls.tasks = load(TASKS)
        cls.vars = cls.plays[0]["vars"]

    def resolve(self, host="s1", registries=..., **registered):
        """Render the play vars in definition order, as Ansible would on host."""
        env = environment()
        context = {"inventory_hostname": host, "groups": GROUPS, **registered}
        if registries is not ...:
            context["k3s_registries"] = registries
        for name, template in self.vars.items():
            context[name] = env.from_string(template).render(context) if isinstance(template, str) else template
        return context

    def errors(self, registries):
        return self.resolve(registries=registries)["k3s_registries_errors"].strip()

    def when_true(self, task, context):
        conditions = task.get("when", [])
        conditions = conditions if isinstance(conditions, list) else [conditions]
        env = environment()
        return all(to_bool(env.compile_expression(str(c))(**context)) for c in conditions)

    def task(self, name):
        return next(t for t in self.tasks if t["name"] == name)

    def evaluate(self, expression, **context):
        return to_bool(environment().compile_expression(str(expression))(**context))

    def index(self, name):
        return next(i for i, t in enumerate(self.tasks) if t["name"] == name)


class StateTests(RegistriesPlaybook):
    def test_unset_or_empty_is_unmanaged(self):
        for registries in (..., None, {}, ""):
            with self.subTest(registries=registries):
                self.assertEqual(self.resolve(registries=registries)["k3s_registries_state"], "unmanaged")

    def test_explicit_states(self):
        self.assertEqual(self.resolve(registries=GHCR)["k3s_registries_state"], "present")
        self.assertEqual(self.resolve(registries={"state": "absent"})["k3s_registries_state"], "absent")

    def test_unit_names(self):
        self.assertEqual(self.resolve(host="s2")["k3s_registries_unit_name"], "k3s")
        self.assertEqual(self.resolve(host="a1")["k3s_registries_unit_name"], "k3s-agent")


class ValidationTests(RegistriesPlaybook):
    def test_valid_configurations_pass(self):
        for registries in (..., None, {}, GHCR, {"state": "absent"},
                           {"configs": {"ghcr.io": {"auth": {"auth": "ZXhhbXBsZQ=="}}}},
                           {"configs": {"docker.io": None, "*": {"tls": {"insecure_skip_verify": True}}}},
                           {"mirrors": {"docker.io": {"endpoint": ["https://mirror.example.com"],
                                                      "rewrite": {"^rancher/(.*)": "mirror/$1"}}}}):
            with self.subTest(registries=registries):
                self.assertEqual(self.errors(registries), "")

    def test_invalid_configurations_fail(self):
        cases = {
            "unknown key k3s_registries.stat": {"stat": "present", **GHCR},
            "state must be present or absent": {"state": "removed"},
            "needs mirrors or configs": {"state": "present"},
            "must be a mapping": ["ghcr.io"],
            "needs a non-empty username and password": {"configs": {"ghcr.io": {"auth": {"username": "example", "password": " "}}}},
            "needs a non-empty username and password ": {"configs": {"ghcr.io": {"auth": {"password": SECRET}}}},
            "needs username and password, auth or identity_token": {"configs": {"ghcr.io": {"auth": {}}}},
            "unknown key k3s_registries.configs[\"ghcr.io\"].auth.passwd": {"configs": {"ghcr.io": {"auth": {"username": "u", "password": "p", "passwd": SECRET}}}},
            "unknown key k3s_registries.configs[\"*\"].tls.insecure": {"configs": {"*": {"tls": {"insecure": True}}}},
            "unknown key k3s_registries.configs[\"ghcr.io\"].tokens": {"configs": {"ghcr.io": {"tokens": SECRET}}},
            "endpoint must be a list of URLs": {"mirrors": {"docker.io": {"endpoint": "https://mirror.example.com"}}},
            "unknown key k3s_registries.mirrors[\"docker.io\"].endpoints": {"mirrors": {"docker.io": {"endpoints": ["https://m"], "endpoint": ["https://m"]}}},
        }
        for expected, registries in cases.items():
            with self.subTest(expected=expected):
                errors = self.errors(registries)
                self.assertIn(expected.strip(), errors)
                self.assertNotIn(SECRET, errors)

    def test_validation_runs_first_on_every_node(self):
        play = self.plays[0]
        self.assertEqual(play["hosts"], "k3s_cluster")
        self.assertIs(play["any_errors_fatal"], True)
        modules = {next(k for k in t if k.startswith("ansible.builtin.")) for t in play["tasks"]}
        self.assertEqual(modules, {"ansible.builtin.assert"})

    def test_collection_variable_is_refused(self):
        guard = next(t for t in self.plays[0]["tasks"] if "registries_config_yaml" in str(t))
        self.assertEqual(guard["ansible.builtin.assert"]["that"], "registries_config_yaml is not defined")


class DisabledTests(RegistriesPlaybook):
    def test_unmanaged_skips_every_node_task(self):
        context = self.resolve(registries=...)
        for task in self.tasks:
            with self.subTest(task=task["name"]):
                self.assertFalse(self.when_true(task, context))

    def test_read_only_tasks_never_report_changes(self):
        for task in self.tasks:
            if "ansible.builtin.command" in task:
                with self.subTest(task=task["name"]):
                    self.assertIs(task["changed_when"], False)


class FileTests(RegistriesPlaybook):
    def test_file_is_root_only_and_hidden_from_logs(self):
        task = self.task("Write the containerd registry configuration")
        copy = task["ansible.builtin.copy"]
        self.assertEqual((copy["owner"], copy["group"], copy["mode"]), ("root", "root", "0600"))
        self.assertIs(task["no_log"], True)
        self.assertIs(task["diff"], False)
        self.assertEqual(self.vars["k3s_registries_path"], "/etc/rancher/k3s/registries.yaml")

    def test_only_the_hidden_task_reads_the_credentials(self):
        for task in self.tasks:
            body = {k: v for k, v in task.items() if k not in ("when", "name")}
            if re.search(r"k3s_registries(?![_a-z])", yaml.safe_dump(body)):
                with self.subTest(task=task["name"]):
                    self.assertIs(task.get("no_log"), True)

    def test_rendered_file_holds_only_mirrors_and_configs(self):
        content = self.task("Write the containerd registry configuration")["ansible.builtin.copy"]["content"]
        registries = {"state": "present", "mirrors": {}, **GHCR}
        rendered = environment(native=False).from_string(content).render(k3s_registries=registries)
        self.assertTrue(rendered.startswith("# Managed by h3xinfra"))
        self.assertEqual(yaml.safe_load(rendered), GHCR)

    def test_absent_removes_the_file(self):
        task = self.task("Remove the containerd registry configuration")
        self.assertEqual(task["ansible.builtin.file"]["state"], "absent")
        self.assertEqual(task["when"], "k3s_registries_state == 'absent'")


class RestartTests(RegistriesPlaybook):
    def context(self, host="s1", registries=GHCR, written=False, removed=False, state="active", started=2000,
                mtime=1000.5, exists=True, containerd_auth=None):
        unit = {"stdout_lines": [f"ActiveState={state}",
                                 f"ActiveEnterTimestamp=@{started}" if started else "ActiveEnterTimestamp="]}
        registered = {"k3s_registries_written": {"changed": written},
                      "k3s_registries_removed": {"changed": removed},
                      "k3s_registries_stat": {"stat": {"exists": exists, "mtime": mtime}},
                      "k3s_registries_unit": unit}
        if containerd_auth is not None:
            registered["k3s_registries_containerd_auth"] = {"rc": 0 if containerd_auth else 1, "stdout": str(containerd_auth)}
        return self.resolve(host=host, registries=registries, **registered)

    def restart(self, **kwargs):
        return self.context(**kwargs)["k3s_registries_restart"]

    def test_restart_decision(self):
        self.assertIs(self.restart(written=True), True)
        self.assertIs(self.restart(registries={"state": "absent"}, removed=True, exists=False), True)
        self.assertIs(self.restart(), False)
        # Written by an earlier run that stopped before the restart.
        self.assertIs(self.restart(mtime=2001.2), True)
        # Same second as the start: systemd prints whole seconds.
        self.assertIs(self.restart(mtime=2000.9), False)
        # K3s is not running (new node, or stopped): never started here.
        self.assertIs(self.restart(written=True, state="inactive", started=None), False)
        self.assertIs(self.restart(registries=None, written=True), False)
        # Removed by an earlier run that stopped before the restart: the
        # generated containerd configuration still holds the credential.
        absent = {"registries": {"state": "absent"}, "exists": False}
        self.assertIs(self.restart(containerd_auth=1, **absent), True)
        self.assertIs(self.restart(containerd_auth=0, **absent), False)
        self.assertIs(self.restart(**absent), False)

    def test_failing_or_changing_unit_stops_the_play(self):
        task = self.task("Refuse a K3s service that is failing or changing state")
        self.assertEqual(task["when"], "k3s_registries_state != 'unmanaged'")
        self.assertLess(self.index("Read the K3s service state"), self.index("Refuse a K3s service that is failing or changing state"))
        self.assertLess(self.index("Refuse a K3s service that is failing or changing state"),
                        self.index("Restart K3s to load the registry configuration"))
        that = task["ansible.builtin.assert"]["that"]
        for state, allowed in (("active", True), ("inactive", True), ("failed", False), ("activating", False),
                               ("deactivating", False), ("reloading", False)):
            with self.subTest(state=state):
                context = self.context(state=state, written=True)
                self.assertEqual(context["k3s_registries_unit_state"], state)
                self.assertIs(self.evaluate(that, **context), allowed)
                if not allowed:
                    self.assertIs(context["k3s_registries_restart"], False)

    def test_absent_counts_credentials_in_the_containerd_configuration(self):
        task = self.task("Count registry credentials in the generated containerd configuration")
        argv = task["ansible.builtin.command"]["argv"]
        self.assertEqual(argv[:4], ["grep", "-c", "-F", "registry.configs."])
        self.assertEqual(argv[4], "{{ k3s_registries_containerd_config }}")
        self.assertEqual(self.vars["k3s_registries_containerd_config"],
                         "/var/lib/rancher/k3s/agent/etc/containerd/config.toml")
        self.assertEqual(task["failed_when"], "k3s_registries_containerd_auth.rc > 1")
        self.assertIs(task["check_mode"], False)
        self.assertTrue(self.when_true(task, self.context(registries={"state": "absent"})))
        self.assertFalse(self.when_true(task, self.context()))
        self.assertFalse(self.when_true(task, self.context(registries={"state": "absent"}, state="inactive")))
        self.assertLess(self.index("Count registry credentials in the generated containerd configuration"),
                        self.index("Restart K3s to load the registry configuration"))

    def test_servers_then_agents_one_at_a_time(self):
        servers, agents = self.plays[1], self.plays[2]
        self.assertEqual((servers["hosts"], agents["hosts"]), ("server", "agent"))
        for play in (servers, agents):
            with self.subTest(play=play["name"]):
                self.assertEqual(play["serial"], 1)
                self.assertEqual(play["max_fail_percentage"], 0)
                self.assertIs(play["become"], True)
                self.assertEqual(play["vars"], self.vars)
                self.assertEqual(play["tasks"], [{"name": "Apply the registry configuration",
                                                  "ansible.builtin.import_tasks": "tasks/registries.yml"}])

    GATES = ("Check that the API server on every other server is ready",
             "Check that every etcd node is Ready and not cordoned",
             "Check that kured is not rebooting a node")

    def test_restart_is_gated_and_followed_by_readiness_waits(self):
        restart = self.task("Restart K3s to load the registry configuration")
        self.assertEqual(restart["ansible.builtin.systemd"],
                         {"name": "{{ k3s_registries_unit_name }}", "state": "restarted"})
        self.assertEqual(restart["when"], "k3s_registries_restart | bool")
        # TimeoutStartSec=0 on the K3s units: a stuck start must fail, not hang.
        self.assertEqual(restart["timeout"], 600)
        at = self.index("Restart K3s to load the registry configuration")
        waits = [self.index(n) for n in ("Read the start time of the restarted K3s",
                                         "Wait for the local kube-apiserver to report ready",
                                         "Wait for the local kubelet to report healthy",
                                         "Wait for the node to be Ready")]
        self.assertTrue(all(self.index(g) < at for g in self.GATES))
        self.assertEqual(waits, sorted(waits))
        self.assertTrue(all(w > at for w in waits))
        for name in self.GATES:
            with self.subTest(gate=name):
                task = self.task(name)
                self.assertIs(task["check_mode"], False)
                self.assertIs(task["changed_when"], False)
                self.assertIn("k3s_registries_restart | bool", task["when"])
        for name in self.GATES[:2] + ("Wait for the local kube-apiserver to report ready",):
            self.assertIn("inventory_hostname in groups.get('server', [])", self.task(name)["when"])
        self.assertEqual(self.task(self.GATES[2])["when"], "k3s_registries_restart | bool")
        self.assertIn("etcd=true", str(self.task(self.GATES[1])))
        self.assertIn("/readyz", str(self.task("Wait for the local kube-apiserver to report ready")))

    def test_every_other_server_must_serve_readyz(self):
        task = self.task("Check that the API server on every other server is ready")
        self.assertEqual(task["ansible.builtin.command"], "k3s kubectl get --raw=/readyz")
        self.assertEqual(task["delegate_to"], "{{ item }}")
        loop = environment().from_string(task["loop"])
        self.assertEqual(loop.render(inventory_hostname="s2", groups=GROUPS), ["s1", "s3"])
        result = "k3s_registries_peer_readyz"
        self.assertTrue(self.evaluate(task["failed_when"], **{result: {"rc": 1, "stdout": ""}}))
        self.assertTrue(self.evaluate(task["failed_when"], **{result: {"rc": 0, "stdout": "[-]etcd failed"}}))
        self.assertFalse(self.evaluate(task["failed_when"], **{result: {"rc": 0, "stdout": "ok"}}))

    def test_etcd_nodes_must_be_ready_and_schedulable(self):
        task = self.task("Check that every etcd node is Ready and not cordoned")
        result = "k3s_registries_etcd_nodes"
        for lines, failed in ((["s1 True ", "s2 True ", "s3 True "], False),
                              (["s1 True", "s2 True false"], False),
                              (["s1 True false", "s2 True "], False),
                              (["s1 True ", "s2 True true"], True),
                              (["s1 True ", "s2 Unknown "], True),
                              (["s1 True ", "s2  "], True),
                              ([], False)):
            with self.subTest(lines=lines):
                self.assertIs(self.evaluate(task["failed_when"], **{result: {"rc": 0, "stdout_lines": lines}}), failed)
        self.assertTrue(self.evaluate(task["failed_when"], **{result: {"rc": 1, "stdout_lines": []}}))
        self.assertIn("{.spec.unschedulable}", task["ansible.builtin.command"]["argv"][-1])

    def test_kured_lock_blocks_the_restart(self):
        task = self.task("Check that kured is not rebooting a node")
        self.assertIn("weave\\.works/kured-node-lock", task["ansible.builtin.command"]["argv"][-1])
        self.assertIn("--selector=app.kubernetes.io/name=kured", task["ansible.builtin.command"]["argv"])
        result = "k3s_registries_kured_lock"
        held = 'kured/h3xinfra-kured-main={"nodeID":"s2","created":"2026-11-11T00:00:00Z"}'
        for lines, failed in ((["kured/h3xinfra-kured-main="], False), ([], False), ([held], True)):
            with self.subTest(lines=lines):
                self.assertIs(self.evaluate(task["failed_when"], **{result: {"rc": 0, "stdout_lines": lines}}), failed)
        self.assertTrue(self.evaluate(task["failed_when"], **{result: {"rc": 1, "stdout_lines": []}}))
        delegate = environment().from_string(task["delegate_to"])
        self.assertEqual(delegate.render(inventory_hostname="a1", groups=GROUPS), "s1")

    def test_ready_wait_needs_a_heartbeat_from_the_restarted_kubelet(self):
        started = self.task("Read the start time of the restarted K3s")
        self.assertIn("--property=ExecMainStartTimestamp", started["ansible.builtin.command"]["argv"])
        start = 1793000000  # 2026-10-26T07:33:20Z
        context = self.resolve(registries=GHCR, k3s_registries_started_at={"rc": 0, "stdout": f"@{start}"})
        self.assertEqual(context["k3s_registries_started_at_utc"], "2026-10-26T07:33:20Z")
        self.assertFalse(self.evaluate(started["failed_when"], k3s_registries_started_at={"rc": 0, "stdout": f"@{start}"}))
        self.assertTrue(self.evaluate(started["failed_when"], k3s_registries_started_at={"rc": 0, "stdout": "n/a"}))
        until = self.task("Wait for the node to be Ready")["until"]
        for stdout, ready in (("True 2026-10-26T07:33:19Z", False),  # posted before the restart
                              ("True 2026-10-26T07:33:20Z", False),  # same second as the start
                              ("True 2026-10-26T07:33:31Z", True),
                              ("False 2026-10-26T07:33:31Z", False),
                              ("True", False)):
            with self.subTest(stdout=stdout):
                self.assertIs(self.evaluate(until, k3s_registries_node_ready={"rc": 0, "stdout": stdout}, **context), ready)

    def test_agent_readiness_is_read_through_a_server(self):
        task = self.task("Wait for the node to be Ready")
        delegate = environment().from_string(task["delegate_to"])
        self.assertEqual(delegate.render(inventory_hostname="a1", groups=GROUPS), "s1")
        self.assertEqual(delegate.render(inventory_hostname="s2", groups=GROUPS), "s2")

    def test_standup_applies_registries_before_the_install(self):
        imports = [p.get("import_playbook") or p.get("ansible.builtin.import_playbook") for p in load(LAYER / "standup.yml")]
        self.assertLess(imports.index("registries.yml"), imports.index("k3s.orchestration.site"))


if __name__ == "__main__":
    unittest.main()
