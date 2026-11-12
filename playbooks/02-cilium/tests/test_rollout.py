"""Check chart-driven agent restarts and per-DaemonSet rollout limits."""

import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


LAYER = Path(__file__).resolve().parents[1]
ROOT = LAYER.parents[1]
HELM = os.environ.get("HELM", "helm")
CHECKSUM = "cilium.io/cilium-configmap-checksum"
STRATEGY = {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 1}}


class CiliumRolloutTests(unittest.TestCase):
    def test_agent_and_envoy_roll_one_node_at_a_time(self):
        values = yaml.safe_load((LAYER / "cilium-values.yaml").read_text())
        for component in (values, values["envoy"]):
            self.assertEqual(component["updateStrategy"], STRATEGY)


class CiliumChartRolloutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Pull the real chart once, at the same pin used by the playbook. A
        # missing tool/chart fails rather than silently skipping rollout proof.
        cls.work = tempfile.TemporaryDirectory(prefix="cilium-rollout-test-")
        cls.addClassCleanup(cls.work.cleanup)
        pins = yaml.safe_load((ROOT / "inventory/production/group_vars/all/versions.yml").read_text())
        version = pins["chart_versions"]["cilium"]
        subprocess.run(
            [HELM, "pull", "cilium", "--repo", "https://helm.cilium.io/", "--version", version,
             "--destination", cls.work.name],
            check=True, capture_output=True, text=True, timeout=120,
        )
        cls.chart = str(Path(cls.work.name) / f"cilium-{version}.tgz")
        metadata = yaml.safe_load(subprocess.check_output(
            [HELM, "show", "chart", cls.chart], text=True, timeout=30,
        ))
        if metadata["name"] != "cilium" or metadata["version"] != version:
            raise AssertionError("render tests must use the pinned Cilium chart")
        cls.values = yaml.safe_load((LAYER / "cilium-values.yaml").read_text())

    def render(self, *, enabled=False, debug=False, rollout=None):
        values = copy.deepcopy(self.values)
        values["hostFirewall"] = {"enabled": enabled}
        values["debug"] = {"enabled": debug}
        # Synthetic dual-stack addresses supply the playbook's site inputs.
        values["ipam"]["operator"] = {
            "clusterPoolIPv4PodCIDRList": ["10.42.0.0/16"],
            "clusterPoolIPv6PodCIDRList": ["2001:db8:cafe::/96"],
            "clusterPoolIPv4MaskSize": 24, "clusterPoolIPv6MaskSize": 112,
        }
        values["ipv4NativeRoutingCIDR"] = "10.42.0.0/16"
        values["ipv6NativeRoutingCIDR"] = "2001:db8:cafe::/96"
        values["extraConfig"]["ipv6-mcast-device"] = "eth0"
        if rollout is not None:
            values["rollOutCiliumPods"] = rollout
        result = subprocess.run(
            [HELM, "template", "h3xinfra-cilium-main", self.chart,
             "--namespace", "kube-system", "-f", "-"],
            input=yaml.safe_dump(values), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return {(doc["kind"], doc["metadata"]["name"]): doc
                for doc in yaml.safe_load_all(result.stdout) if doc}

    def test_adoption_rolls_agents_even_with_firewall_disabled(self):
        old = self.render(rollout=False)
        new = self.render()
        agent = ("DaemonSet", "cilium")
        config = ("ConfigMap", "cilium-config")
        old_annotations = old[agent]["spec"]["template"]["metadata"]["annotations"]
        new_annotations = new[agent]["spec"]["template"]["metadata"]["annotations"]
        self.assertNotIn(CHECKSUM, old_annotations)
        self.assertIn(CHECKSUM, new_annotations)
        self.assertRegex(new_annotations[CHECKSUM], r"^[0-9a-f]{64}$")
        self.assertEqual(new[config]["data"]["enable-host-firewall"], "false")
        self.assertEqual(old[config], new[config])
        # Adoption changes only the agent template's checksum annotation.
        expected = copy.deepcopy(new)
        del expected[agent]["spec"]["template"]["metadata"]["annotations"][CHECKSUM]
        self.assertEqual(old, expected)

    def test_firewall_toggle_and_other_config_changes_roll_agents(self):
        baseline = self.render()
        agent = ("DaemonSet", "cilium")
        config = ("ConfigMap", "cilium-config")
        base_template = baseline[agent]["spec"]["template"]
        for options, key, value in (
            ({"enabled": True}, "enable-host-firewall", "true"),
            ({"debug": True}, "debug", "true"),
        ):
            with self.subTest(options=options):
                changed = self.render(**options)
                template = changed[agent]["spec"]["template"]
                self.assertEqual(changed[config]["data"][key], value)
                self.assertNotEqual(baseline[config], changed[config])
                self.assertNotEqual(base_template, template)
                self.assertNotEqual(base_template["metadata"]["annotations"][CHECKSUM],
                                    template["metadata"]["annotations"][CHECKSUM])
        # Returning to disabled produces the same template, so an on -> off
        # upgrade also replaces the enabled template without a custom marker.
        self.assertEqual(baseline, self.render())

    def test_rendered_agent_and_envoy_roll_one_at_a_time(self):
        documents = self.render()
        for name in ("cilium", "cilium-envoy"):
            with self.subTest(daemonset=name):
                self.assertEqual(documents["DaemonSet", name]["spec"]["updateStrategy"], STRATEGY)


if __name__ == "__main__":
    unittest.main()
