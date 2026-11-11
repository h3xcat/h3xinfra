"""Static checks for the host sysctls that 00-prep sets."""

from pathlib import Path
import re
import unittest

import yaml


PREP = Path(__file__).resolve().parents[1]
PLAYBOOKS = PREP.parent
# A src_valid_mark entry set to 1: `name: ..., value: "1"`, `...=1` or `...: 1`.
SETS_ONE = re.compile(r"src_valid_mark\b[\"']?\s*(?:,\s*value)?\s*[:=]\s*[\"']?1\b")


def sysctl_items(playbook):
    """Yield (name, value) for every ansible.posix.sysctl entry of a playbook."""
    for play in playbook:
        for task in play.get("tasks", []) + play.get("pre_tasks", []) + play.get("post_tasks", []):
            for step in [task] + task.get("block", []):
                module = step.get("ansible.posix.sysctl") or step.get("sysctl")
                if not module:
                    continue
                for item in step.get("loop", [None]):
                    name = item["name"] if item and module["name"] == "{{ item.name }}" else module["name"]
                    value = item["value"] if item and module["value"] == "{{ item.value }}" else module["value"]
                    yield name, str(value)


class SrcValidMarkTests(unittest.TestCase):
    """src_valid_mark=1 on the host breaks Cilium's IPv4 transparent DNS proxy.

    The reverse-path check then uses the TPROXY mark 0x200, finds the local
    route of Cilium's policy routing table for the pod address and drops the
    redirected DNS query as a martian source. WireGuard pods set the sysctl in
    their own network namespace (pod securityContext.sysctls, allowed by
    kubelet_allowed_unsafe_sysctls in 01-k3s).
    """

    def test_prep_resets_src_valid_mark_to_zero(self):
        items = [(n, v) for n, v in sysctl_items(yaml.safe_load((PREP / "standup.yml").read_text()))
                 if n.endswith(".src_valid_mark")]
        # Set explicitly, so a node that still has 1 is reset live and in
        # /etc/sysctl.conf.
        self.assertEqual(items, [("net.ipv4.conf.all.src_valid_mark", "0")])

    def test_no_playbook_sets_src_valid_mark_to_one_on_a_host(self):
        for path in sorted(PLAYBOOKS.rglob("*.yml")) + sorted(PLAYBOOKS.rglob("*.yaml")):
            if "/charts/" in str(path) or "/tests/" in str(path):
                continue
            # Comments may explain the setting; only YAML content counts.
            content = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())
            with self.subTest(path=str(path.relative_to(PLAYBOOKS))):
                self.assertIsNone(SETS_ONE.search(content))

    def test_pattern_catches_the_old_setting(self):
        for text in ('- { name: net.ipv4.conf.all.src_valid_mark, value: "1" }',
                     "sysctl -w net.ipv4.conf.all.src_valid_mark=1",
                     "net.ipv4.conf.all.src_valid_mark: 1"):
            with self.subTest(text=text):
                self.assertIsNotNone(SETS_ONE.search(text))
        self.assertIsNone(SETS_ONE.search('- { name: net.ipv4.conf.all.src_valid_mark, value: "0" }'))


if __name__ == "__main__":
    unittest.main()
