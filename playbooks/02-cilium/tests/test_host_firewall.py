"""Check the opt-in Cilium host firewall wiring without cluster access."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


LAYER = Path(__file__).resolve().parents[1]
CHART = LAYER / "charts/h3xinfra-cilium-post"
HELM = os.environ.get("HELM") or shutil.which("helm")
ANSIBLE = shutil.which("ansible-playbook")
LABEL = "h3xinfra.io/host-firewall"
STARTED = "h3xinfra.io/host-firewall-enabled"
RESTART = "Restart the Cilium agents to apply the host firewall setting"


def standup_play():
    plays = yaml.safe_load((LAYER / "standup.yml").read_text())
    return next(play for play in plays if play.get("name") == "Deploy Cilium via Helm")


def task(play, name):
    return next(item for item in play["tasks"] if item.get("name") == name)


def render(values):
    result = subprocess.run(
        [HELM, "template", "h3xinfra-cilium-post", str(CHART), "--namespace", "kube-system", "-f", "-"],
        input=yaml.safe_dump(values), capture_output=True, text=True, timeout=60,
    )
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc] if result.returncode == 0 else None
    return result, docs


class HostFirewallPlaybookTests(unittest.TestCase):
    def test_agent_flag_follows_the_inventory_switch(self):
        helm = task(standup_play(), "Deploy Cilium via Helm")["kubernetes.core.helm"]
        values = [item["value"] for item in helm["set_values"]]
        self.assertIn("hostFirewall.enabled={{ host_fw_enabled | bool | lower }}", values)

    def test_switch_and_node_list_default_to_off(self):
        play_vars = standup_play()["vars"]
        self.assertIn("default(false)", play_vars["host_fw_enabled"])
        self.assertIn("default([])", play_vars["host_fw_nodes"])

    def test_policy_release_uses_the_same_switch_and_label(self):
        helm = task(standup_play(), "Deploy Cilium host firewall policy via Helm")["kubernetes.core.helm"]
        self.assertEqual(helm["chart_ref"], "{{ playbook_dir }}/charts/h3xinfra-cilium-post")
        fw = helm["values"]["hostFirewall"]
        self.assertEqual(fw["enabled"], "{{ host_fw_enabled | bool }}")
        self.assertEqual(fw["nodeLabel"], {"key": "{{ host_fw_label_key }}", "value": "{{ host_fw_label_value }}"})
        self.assertEqual(standup_play()["vars"]["host_fw_label_key"], LABEL)

    def test_settings_are_checked_before_any_cluster_change(self):
        names = [item["name"] for item in standup_play()["tasks"]]
        first_change = names.index("Add Cilium Helm repository")
        for name in ("Validate the Cilium host firewall settings", "Check that every host firewall node exists"):
            self.assertLess(names.index(name), first_change)
        self.assertLess(names.index("Read the host firewall setting in cilium-config before the upgrade"),
                        names.index("Deploy Cilium via Helm"))
        self.assertLess(names.index("Deploy Cilium via Helm"), names.index("Read the Cilium agent DaemonSet"))
        self.assertLess(names.index("Read the Cilium agent DaemonSet"), names.index(RESTART))
        self.assertLess(names.index(RESTART), names.index("Deploy Cilium host firewall policy via Helm"))
        self.assertLess(names.index("Deploy Cilium host firewall policy via Helm"),
                        names.index("Label the nodes that enforce the host firewall policy"))

    def test_restart_patches_the_agent_pod_template_and_waits(self):
        play = standup_play()
        restart = task(play, RESTART)
        k8s = restart["kubernetes.core.k8s"]
        self.assertEqual((k8s["kind"], k8s["name"], k8s["state"]), ("DaemonSet", "{{ host_fw_agent_daemonset }}", "patched"))
        self.assertIn("{'spec': {'template': {'metadata': {'annotations': {host_fw_started_key: host_fw_desired}}}}}",
                      k8s["definition"])
        self.assertTrue(k8s["wait"])
        self.assertEqual(restart["when"], "host_fw_restart_needed | bool")
        self.assertEqual(play["vars"]["host_fw_started_key"], STARTED)
        self.assertEqual(play["vars"]["host_fw_agent_daemonset"], "cilium")
        self.assertEqual(play["vars"]["host_fw_agent_config_map"], "cilium-config")

    def test_teardown_removes_policy_release_before_cilium(self):
        plays = yaml.safe_load((LAYER / "teardown.yml").read_text())
        names = [item["kubernetes.core.helm"]["name"] for play in plays for item in play.get("tasks", [])
                 if "kubernetes.core.helm" in item]
        self.assertEqual(names, ["{{ cilium.release_name_prefix }}-post", "{{ cilium.release_name_prefix }}-main"])


@unittest.skipUnless(ANSIBLE, "ansible-playbook is not installed")
class HostFirewallPlaybookLogicTests(unittest.TestCase):
    """Run the play vars, the validation and the label conditions on localhost."""

    NODES = [
        {"metadata": {"name": "node-a", "labels": {}}},
        {"metadata": {"name": "node-b", "labels": {LABEL: "enforce"}}},
        {"metadata": {"name": "node-c"}},
    ]

    def evaluate(self, host_firewall, config_before=None, started=None, annotations=True):
        """Run the play logic. config_before is enable-host-firewall in
        cilium-config before the upgrade (None: no ConfigMap). started is the
        agent pod template annotation (None: not set)."""
        play = standup_play()
        tasks = [copy.deepcopy(task(play, name)) for name in (
            "Validate the Cilium host firewall settings",
            "Check that every host firewall node exists",
            "Check that the Cilium agent DaemonSet exists",
        )]
        for name, key in ((RESTART, None),
                          ("Label the nodes that enforce the host firewall policy", "add"),
                          ("Remove the host firewall label from all other nodes", "remove")):
            item = copy.deepcopy(task(play, name))
            module = item.pop("kubernetes.core.k8s")
            if key is None:
                # Keep the real patch body. A nested value keeps its type.
                item["ansible.builtin.set_fact"] = {"restart": module["definition"]}
            else:
                item["ansible.builtin.set_fact"] = {key: "{{ (" + key + " | default([])) + [item.metadata.name] }}"}
            tasks.append(item)
        config_maps = [] if config_before is None else [{"data": {"enable-host-firewall": config_before}}]
        template_meta = {}
        if started is not None:
            template_meta["annotations"] = {STARTED: started}
        elif annotations is None:
            template_meta["annotations"] = None
        daemonset = {"spec": {"template": {"metadata": template_meta}}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            tasks.append({"ansible.builtin.copy": {
                "dest": str(out),
                "content": "{{ {'add': add | default([]), 'remove': remove | default([]),"
                           " 'restart': restart | default(none),"
                           " 'trusted': host_fw_trusted_cidrs, 'enabled': host_fw_enabled | bool} | to_json }}",
            }})
            cilium = {"namespace": "kube-system", "release_name_prefix": "test"}
            if host_firewall is not None:
                cilium["host_firewall"] = host_firewall
            vars_ = dict(play["vars"], cilium=cilium, host_fw_node_info={"resources": self.NODES},
                         host_fw_config_before={"resources": config_maps},
                         host_fw_agent_ds_info={"resources": [daemonset]})
            # An empty config keeps the repository ansible.cfg (inventory,
            # vault password script) out of this local run.
            config = Path(tmp) / "ansible.cfg"
            config.write_text("[defaults]\n")
            playbook = Path(tmp) / "play.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "connection": "local", "gather_facts": False,
                "vars": vars_, "tasks": tasks,
            }]))
            result = subprocess.run(
                [ANSIBLE, "-i", "localhost,", str(playbook)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                env=dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_CONFIG=str(config)),
            )
            return result, (json.loads(out.read_text()) if out.exists() else None)

    def test_default_is_off_and_removes_stale_labels(self):
        result, data = self.evaluate(None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(data["enabled"])
        self.assertEqual(data["add"], [])
        self.assertEqual(data["remove"], ["node-b"])

    def test_listed_nodes_are_labeled_and_others_unlabeled(self):
        result, data = self.evaluate({
            "enabled": True, "enforce_nodes": ["node-a"],
            "extra_trusted_cidrs": {"ipv6": ["2001:db8:1::/48"]},
        })
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(data["add"], ["node-a"])
        self.assertEqual(data["remove"], ["node-b"])
        self.assertEqual(data["trusted"]["ipv4"], ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"])
        self.assertEqual(data["trusted"]["ipv6"], ["fc00::/7", "fe80::/10", "::/128", "2001:db8:1::/48"])

    def test_already_labeled_node_is_not_patched_again(self):
        result, data = self.evaluate({"enabled": True, "enforce_nodes": ["node-b"]})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(data["add"], [])
        self.assertEqual(data["remove"], [])

    def test_default_deploy_does_not_restart_the_agents(self):
        for config_before in (None, "false"):
            with self.subTest(config_before=config_before):
                result, data = self.evaluate(None, config_before=config_before)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIsNone(data["restart"])
        result, data = self.evaluate(None, config_before="false", annotations=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIsNone(data["restart"])

    def test_agents_restart_only_when_the_setting_changes(self):
        on = {"enabled": True}
        off = {"enabled": False}
        cases = (
            # (settings, cilium-config before, annotation, expected restart)
            (on, "false", None, "true"),     # first turn-on
            (on, "true", None, "true"),      # turn-on run stopped before the restart
            (on, None, None, "true"),        # new install
            (on, "true", "true", None),      # no change
            (on, "true", "false", "true"),   # turn on again
            (off, "true", "true", "false"),  # rollback
            (off, "true", None, "false"),    # turned on outside this play
            (off, "false", "false", None),   # no change
            (off, "false", "true", "false"),  # rollback run stopped before the restart
        )
        for settings, config_before, started, expected in cases:
            with self.subTest(settings=settings, config_before=config_before, started=started):
                result, data = self.evaluate(settings, config_before=config_before, started=started)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if expected is None:
                    self.assertIsNone(data["restart"])
                else:
                    # The annotation value must be a string, not a boolean.
                    self.assertEqual(data["restart"],
                                     {"spec": {"template": {"metadata": {"annotations": {STARTED: expected}}}}})

    def test_unknown_node_name_fails(self):
        result, data = self.evaluate({"enabled": True, "enforce_nodes": ["node-a", "node-x"]})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown node names", result.stdout)
        self.assertIsNone(data)

    def test_invalid_settings_fail_before_any_change(self):
        for host_firewall in (
            {"enabled": False, "enforce_nodes": ["node-a"]},
            {"enabled": True, "enforce_nodes": "node-a"},
            {"enabled": True, "extra_trusted_cidrs": {"ipv4": ["2001:db8::/32"]}},
            {"enabled": True, "extra_trusted_cidrs": {"ipv6": ["192.0.2.0/24"]}},
            {"enabled": True, "extra_trusted_cidrs": {"ipv6": ["2001:db8::/129"]}},
            {"enabled": True, "extra_trusted_cidrs": {"ipv6": ["2001:db8::"]}},
            {"enabled": True, "extra_trusted_cidrs": {"ipv4": ["192.0.2.0/33"]}},
        ):
            with self.subTest(host_firewall=host_firewall):
                result, data = self.evaluate(host_firewall)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cilium.host_firewall: enforce_nodes must be a list", result.stdout)
                self.assertIsNone(data)


@unittest.skipUnless(HELM, "helm is not installed")
class HostFirewallChartTests(unittest.TestCase):
    TRUSTED = {"ipv4": ["10.0.0.0/8", "192.168.0.0/16"], "ipv6": ["fc00::/7", "fe80::/10", "::/128"]}

    def test_disabled_chart_renders_nothing(self):
        result, docs = render({})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(docs, [])

    def test_enabled_chart_renders_deny_only_host_policy(self):
        result, docs = render({"hostFirewall": {"enabled": True, "trustedCIDRs": self.TRUSTED}})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(docs), 1)
        policy = docs[0]
        self.assertEqual(policy["kind"], "CiliumClusterwideNetworkPolicy")
        spec = policy["spec"]
        self.assertEqual(spec["nodeSelector"], {"matchLabels": {LABEL: "enforce"}})
        self.assertNotIn("endpointSelector", spec)
        # Deny rules only: no allow section and no default-deny transition.
        self.assertEqual(spec["enableDefaultDeny"], {"ingress": False, "egress": False})
        self.assertNotIn("ingress", spec)
        self.assertNotIn("egress", spec)
        self.assertNotIn("egressDeny", spec)
        self.assertEqual(spec["ingressDeny"], [{"fromCIDRSet": [
            {"cidr": "0.0.0.0/0", "except": self.TRUSTED["ipv4"]},
            {"cidr": "::/0", "except": self.TRUSTED["ipv6"]},
        ]}])

    def test_enabled_chart_requires_trusted_ranges_for_both_families(self):
        for trusted in ({"ipv4": [], "ipv6": ["fc00::/7"]}, {"ipv4": ["10.0.0.0/8"], "ipv6": []}):
            with self.subTest(trusted=trusted):
                result, _ = render({"hostFirewall": {"enabled": True, "trustedCIDRs": trusted}})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must each list at least one CIDR", result.stderr)


if __name__ == "__main__":
    unittest.main()
