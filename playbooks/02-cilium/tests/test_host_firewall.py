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
        self.assertLess(names.index("Deploy Cilium via Helm"),
                        names.index("Deploy Cilium host firewall policy via Helm"))
        for name in ("Label the nodes that enforce the host firewall policy",
                     "Remove the host firewall label from all other nodes"):
            self.assertLess(names.index("Deploy Cilium host firewall policy via Helm"), names.index(name))

    def test_helm_waits_for_the_rollout_before_policy_and_labels(self):
        helm = task(standup_play(), "Deploy Cilium via Helm")["kubernetes.core.helm"]
        self.assertIs(helm["wait"], True)
        self.assertTrue(helm["timeout"].endswith("s"))
        self.assertGreaterEqual(int(helm["timeout"][:-1]), 1800)

    def test_teardown_removes_policy_release_before_cilium(self):
        plays = yaml.safe_load((LAYER / "teardown.yml").read_text())
        self.assertEqual(plays[0], {"name": "Require confirmation before teardown",
                                   "import_playbook": "../99-utils/confirm-destroy.yml"})
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

    def evaluate(self, host_firewall):
        """Run the retained input validation and node-label logic locally."""
        play = standup_play()
        tasks = [copy.deepcopy(task(play, name)) for name in (
            "Validate the Cilium host firewall settings",
            "Check that every host firewall node exists",
        )]
        for name, key in (("Label the nodes that enforce the host firewall policy", "add"),
                          ("Remove the host firewall label from all other nodes", "remove")):
            item = copy.deepcopy(task(play, name))
            item.pop("kubernetes.core.k8s")
            item["ansible.builtin.set_fact"] = {key: "{{ (" + key + " | default([])) + [item.metadata.name] }}"}
            tasks.append(item)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            tasks.append({"ansible.builtin.copy": {
                "dest": str(out),
                "content": "{{ {'add': add | default([]), 'remove': remove | default([]),"
                           " 'trusted': host_fw_trusted_cidrs, 'enabled': host_fw_enabled | bool} | to_json }}",
            }})
            cilium = {"namespace": "kube-system", "release_name_prefix": "test"}
            if host_firewall is not None:
                cilium["host_firewall"] = host_firewall
            vars_ = dict(play["vars"], cilium=cilium, host_fw_node_info={"resources": self.NODES})
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
