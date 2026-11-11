"""Static checks for kubelet-sysctls.yml (kubelet allowlist for unsafe sysctls).

The play vars and the task templates are rendered with plain Jinja2 plus
small stand-ins for the Ansible filters and tests that they use. No hosts are
contacted.
"""

import base64
import json
from pathlib import Path
import sys
import unittest

import jinja2
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playbook_helpers import (  # noqa: E402
    IMPORT, LAYER, environment, evaluate, flatten, load, render_vars, service_facts, when_true,
)


PLAYBOOK = LAYER / "kubelet-sysctls.yml"
CHECK = LAYER / "tasks" / "kubelet-sysctls-check.yml"
DROPIN_TASKS = LAYER / "tasks" / "kubelet-sysctls.yml"
NODE_TASKS = LAYER / "tasks" / "kubelet-sysctls-node.yml"
DROPIN = "/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/99-unsafe-sysctls.conf"
GROUPS = {"server": ["s1", "s2", "s3"], "agent": ["a1"], "k3s_cluster": ["s1", "s2", "s3", "a1"]}
WIREGUARD = ["net.ipv4.conf.all.src_valid_mark"]
# What K3s writes into 00-k3s-defaults.conf when its servicelb can run
# (k3s pkg/daemons/agent/agent_linux.go).
K3S_DEFAULTS = ["net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding"]
RESTART_TASK = "Restart K3s to load the new configuration"

# The kernel.* sysctls that the kubelet knows to be in the IPC namespace
# (k8s.io/component-helpers/node/util/sysctl/namespace.go). Any other
# kernel.* name makes the kubelet refuse to start.
KUBELET_IPC_SYSCTLS = ["kernel.sem", "kernel.shmall", "kernel.shmmax", "kernel.shmmni", "kernel.shm_rmid_forced",
                       "kernel.msgmax", "kernel.msgmnb", "kernel.msgmni"]


def check_passes(names=...):
    task = load(CHECK)[0]
    context = {"inventory_hostname": "s1"}
    if names is not ...:
        context["kubelet_allowed_unsafe_sysctls"] = names
    context = render_vars(task["vars"], context)
    try:
        return all(evaluate(c, **context) for c in task["ansible.builtin.assert"]["that"])
    except TypeError:
        return False  # a non-string entry: the regex test fails the task


def configz(names):
    config = {"kubeletconfig": {"maxPods": 110}}
    if names:
        config["kubeletconfig"]["allowedUnsafeSysctls"] = names
    return {"rc": 0, "stdout": json.dumps(config)}


def k3s_defaults(names):
    content = "apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\n"
    if names:
        content += "allowedUnsafeSysctls:\n" + "".join(f"- {n}\n" for n in names)
    return {"content": base64.b64encode(content.encode()).decode()}


class ValidationTests(unittest.TestCase):
    def test_valid_lists_pass(self):
        for names in (..., None, [], "", WIREGUARD, KUBELET_IPC_SYSCTLS,
                      ["net.core.somaxconn", "net.ipv6.conf.all.disable_ipv6", "fs.mqueue.msg_max",
                       "net.ipv4.conf.eth0.rp_filter", "net.ipv4.tcp_keepalive_time"]):
            with self.subTest(names=names):
                self.assertTrue(check_passes(names))

    def test_invalid_lists_fail(self):
        for names in (
            "net.ipv4.conf.all.src_valid_mark",                 # not a list
            {"net.ipv4.conf.all.src_valid_mark": 1},            # not a list
            ["net.core.somaxconn", 1],                          # not a name
            ["net.ipv4.*"], ["kernel.shm*"],                    # patterns
            ["kernel.panic"], ["vm.swappiness"],                # not namespaced: the kubelet would not start
            ["kernel.shmfoo"],                                  # the kubelet knows only the exact IPC names
            ["kernel.domainname"], ["user.max_user_namespaces"],
            ["net/ipv4/conf/all/src_valid_mark"], ["NET.ipv4.ip_forward"],
            ["net."], ["net"], ["fs.mqueue"], ["net.ipv4..x"],
        ):
            with self.subTest(names=names):
                self.assertFalse(check_passes(names))

    def test_accepted_kernel_names_are_the_kubelet_ipc_names(self):
        for name in KUBELET_IPC_SYSCTLS:
            with self.subTest(name=name):
                self.assertTrue(check_passes([name]))

    def test_check_stops_every_host(self):
        self.assertIs(load(CHECK)[0]["any_errors_fatal"], True)


class DropinTests(unittest.TestCase):
    def setUp(self):
        self.tasks = load(DROPIN_TASKS)
        self.write = next(t for t in self.tasks if "ansible.builtin.copy" in t)
        self.remove = next(t for t in self.tasks if t.get("ansible.builtin.file", {}).get("state") == "absent")

    def render(self, names):
        # Ansible renders templates with trim_blocks.
        env = jinja2.Environment(trim_blocks=True)
        return env.from_string(self.write["ansible.builtin.copy"]["content"]).render(kubelet_allowed_unsafe_sysctls=names)

    def test_dropin_path_and_mode(self):
        copy = self.write["ansible.builtin.copy"]
        # kubelet --config-dir reads only *.conf files, in name order; 99
        # sorts after 00-k3s-defaults.conf.
        self.assertEqual(copy["dest"], DROPIN)
        self.assertEqual((copy["owner"], copy["group"], copy["mode"]), ("root", "root", "0644"))
        self.assertEqual(self.remove["ansible.builtin.file"]["path"], DROPIN)

    def test_rendered_file_is_a_kubelet_configuration(self):
        rendered = self.render(["net.ipv4.conf.all.src_valid_mark", "net.core.somaxconn", "net.core.somaxconn"])
        self.assertTrue(rendered.startswith("# Managed by h3xinfra"))
        self.assertEqual(yaml.safe_load(rendered), {
            "apiVersion": "kubelet.config.k8s.io/v1beta1",
            "kind": "KubeletConfiguration",
            "allowedUnsafeSysctls": ["net.core.somaxconn", "net.ipv4.conf.all.src_valid_mark"],
        })

    def test_empty_list_removes_the_file(self):
        for names, written in ((WIREGUARD, True), ([], False), (None, False)):
            with self.subTest(names=names):
                context = {"kubelet_allowed_unsafe_sysctls": names}
                self.assertIs(when_true(self.write, context), written)
                self.assertIs(when_true(self.remove, context), not written)
        self.assertIs(when_true(self.write, {}), False)
        self.assertIs(when_true(self.remove, {}), True)

    def test_dropin_tasks_never_restart(self):
        modules = {next(k for k in t if k.startswith("ansible.")) for t in self.tasks}
        self.assertEqual(modules, {"ansible.builtin.file", "ansible.builtin.copy"})


class KubeletSysctlsPlaybook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plays = load(PLAYBOOK)
        cls.vars = cls.plays[1]["vars"]
        cls.tasks = flatten(NODE_TASKS)

    def task(self, name):
        return next(t for t in self.tasks if t["name"] == name)

    def index(self, name):
        return next(i for i, t in enumerate(self.tasks) if t["name"] == name)


class PlaybookTests(KubeletSysctlsPlaybook):
    def test_validation_runs_first_on_every_node(self):
        play = self.plays[0]
        self.assertEqual(play["hosts"], "k3s_cluster")
        self.assertIs(play["any_errors_fatal"], True)
        self.assertEqual(play["tasks"], [{"name": "Check the allowlist", IMPORT: "tasks/kubelet-sysctls-check.yml"}])

    def test_servers_then_agents_one_at_a_time(self):
        servers, agents = self.plays[1], self.plays[2]
        self.assertEqual((servers["hosts"], agents["hosts"]), ("server", "agent"))
        for play in (servers, agents):
            with self.subTest(play=play["name"]):
                self.assertEqual(play["serial"], 1)
                self.assertEqual(play["max_fail_percentage"], 0)
                self.assertIs(play["become"], True)
                self.assertEqual(play["vars"], self.vars)
                self.assertEqual(play["tasks"], [{"name": "Apply the allowlist", IMPORT: "tasks/kubelet-sysctls-node.yml"}])

    def test_node_steps_write_then_read_then_restart_then_verify(self):
        imports = [t[IMPORT] for t in load(NODE_TASKS) if IMPORT in t]
        self.assertEqual(imports, ["kubelet-sysctls.yml", "k3s-service-state.yml", "restart-k3s.yml"])
        order = ["Write the kubelet unsafe sysctl allowlist", "Read the K3s service state",
                 "Refuse a K3s service that is failing or changing state",
                 "Read the configuration of the running kubelet", "Check that kured is not rebooting a node",
                 RESTART_TASK, "Wait for the node to be Ready", "Read the configuration of the restarted kubelet",
                 "Check that the restarted kubelet has the new allowlist"]
        indexes = [self.index(n) for n in order]
        self.assertEqual(indexes, sorted(indexes))

    def test_unit_names(self):
        for host, unit in (("s2", "k3s"), ("a1", "k3s-agent")):
            with self.subTest(host=host):
                context = render_vars(self.vars, {"inventory_hostname": host, "groups": GROUPS})
                self.assertEqual(context["kubelet_sysctls_unit_name"], unit)
        restart = self.task(RESTART_TASK)
        self.assertEqual(restart["vars"], {"k3s_service_name": "{{ kubelet_sysctls_unit_name }}",
                                           "k3s_restart": "{{ kubelet_sysctls_restart }}"})
        self.assertEqual(self.task("Read the K3s service state")["vars"],
                         {"k3s_service_name": "{{ kubelet_sysctls_unit_name }}"})

    def test_read_only_tasks_never_report_changes(self):
        for task in self.tasks:
            if "ansible.builtin.command" in task:
                with self.subTest(task=task["name"]):
                    self.assertIs(task["changed_when"], False)


class RestartTests(KubeletSysctlsPlaybook):
    def context(self, names=WIREGUARD, live=K3S_DEFAULTS, defaults=K3S_DEFAULTS, state="active", host="a1"):
        registered = {"kubelet_allowed_unsafe_sysctls": names, "inventory_hostname": host, "groups": GROUPS,
                      **service_facts(state)}
        if state == "active":
            registered["kubelet_sysctls_configz"] = configz(live)
            if not names:
                registered["kubelet_sysctls_k3s_defaults"] = k3s_defaults(defaults)
        else:
            registered["kubelet_sysctls_configz"] = {"skipped": True}
        return render_vars(self.vars, registered)

    def restart(self, **kwargs):
        return self.context(**kwargs)["kubelet_sysctls_restart"]

    def test_restart_decision(self):
        # New list on an agent that runs with the K3s defaults.
        self.assertIs(self.restart(), True)
        # The running kubelet already has the list (also after standup.yml).
        self.assertIs(self.restart(live=WIREGUARD), False)
        self.assertIs(self.restart(names=["net.b", "net.a"], live=["net.a", "net.b"]), False)
        # An earlier run wrote the file and stopped before the restart.
        self.assertIs(self.restart(live=[]), True)
        # Empty list: the K3s defaults apply again.
        self.assertIs(self.restart(names=[], live=WIREGUARD), True)
        self.assertIs(self.restart(names=[], live=K3S_DEFAULTS), False)
        self.assertIs(self.restart(names=None, live=[], defaults=[], host="s1"), False)
        self.assertIs(self.restart(names=[], live=WIREGUARD, defaults=[], host="s1"), True)
        # K3s is not running (new node, or stopped): never started here.
        self.assertIs(self.restart(state="inactive"), False)

    def test_restart_follows_the_decision(self):
        restart = self.task(RESTART_TASK)
        self.assertEqual(restart["when"], ["k3s_restart | bool"])
        self.assertTrue(when_true(restart, self.context()))
        self.assertFalse(when_true(restart, self.context(live=WIREGUARD)))
        self.assertFalse(when_true(restart, self.context(state="inactive")))

    def test_decision_does_not_change_during_the_restart(self):
        decision = " ".join(str(self.vars[n]) for n in
                            ("kubelet_sysctls_expected", "kubelet_sysctls_live", "kubelet_sysctls_restart"))
        for task in self.tasks[self.index(RESTART_TASK):]:
            if "register" in task:
                with self.subTest(register=task["register"]):
                    self.assertNotIn(task["register"], decision)

    def test_reads_are_skipped_when_k3s_is_stopped(self):
        context = self.context(state="inactive")
        for name in ("Read the node name for the kubelet configuration", "Read the K3s kubelet defaults",
                     "Read the configuration of the running kubelet"):
            with self.subTest(task=name):
                self.assertFalse(when_true(self.task(name), context))
        self.assertTrue(when_true(self.task("Read the K3s kubelet defaults"), self.context(names=[])))
        self.assertFalse(when_true(self.task("Read the K3s kubelet defaults"), self.context()))

    def test_configz_is_read_through_a_server(self):
        for name in ("Read the configuration of the running kubelet", "Read the configuration of the restarted kubelet"):
            task = self.task(name)
            with self.subTest(task=name):
                self.assertIn("/proxy/configz", task["ansible.builtin.command"]["argv"][-1])
                delegate = environment().from_string(task["delegate_to"])
                self.assertEqual(delegate.render(inventory_hostname="a1", groups=GROUPS), "s1")
                self.assertEqual(delegate.render(inventory_hostname="s2", groups=GROUPS), "s2")

    def test_restart_is_guarded(self):
        for name in ("Check that the API server on every other server is ready",
                     "Check that every etcd node is Ready and not cordoned",
                     "Check that kured is not rebooting a node"):
            with self.subTest(gate=name):
                self.assertLess(self.index(name), self.index(RESTART_TASK))
                self.assertIn("k3s_restart | bool", self.task(name)["when"])

    def test_restarted_kubelet_must_have_the_new_list(self):
        verify = self.task("Check that the restarted kubelet has the new allowlist")
        read = self.task("Read the configuration of the restarted kubelet")
        for task in (read, verify):
            with self.subTest(task=task["name"]):
                self.assertEqual(task["when"], ["kubelet_sysctls_restart | bool", "not ansible_check_mode"])
        context = self.context()
        for live, ok in ((WIREGUARD, True), (K3S_DEFAULTS, False), ([], False)):
            with self.subTest(live=live):
                checked = render_vars(verify["vars"], {**context, "kubelet_sysctls_configz_after": configz(live)})
                self.assertIs(evaluate(verify["ansible.builtin.assert"]["that"], **checked), ok)


class StandupTests(unittest.TestCase):
    def setUp(self):
        self.standup = load(LAYER / "standup.yml")
        self.kubelet_play = next(p for p in self.standup if p.get("name") == "Create kubelet configuration")

    def test_standup_checks_first_and_writes_the_dropin(self):
        tasks = self.kubelet_play["tasks"]
        self.assertEqual(tasks[0][IMPORT], "tasks/kubelet-sysctls-check.yml")
        self.assertIn("tasks/kubelet-sysctls.yml", [t.get(IMPORT) for t in tasks])

    def test_standup_writes_before_the_install_and_does_not_restart(self):
        # The K3s install restarts every node, so the kubelet loads the file
        # then. Importing kubelet-sysctls.yml as well would restart twice.
        imports = [p.get("import_playbook") or p.get("ansible.builtin.import_playbook") for p in self.standup]
        self.assertNotIn("kubelet-sysctls.yml", imports)
        self.assertLess(self.standup.index(self.kubelet_play), imports.index("k3s.orchestration.site"))
        imported = [t.get(IMPORT) for t in self.kubelet_play["tasks"]]
        self.assertNotIn("tasks/restart-k3s.yml", imported)


if __name__ == "__main__":
    unittest.main()
