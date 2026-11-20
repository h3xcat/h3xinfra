"""Check the Mailu chart values in standup.yml that limit Redis and Sieve."""
from pathlib import Path
import re
import unittest

import yaml

LAYER = Path(__file__).resolve().parents[1]


def main_values():
    for play in yaml.safe_load((LAYER / "standup.yml").read_text()):
        for task in play.get("tasks", []):
            if task.get("name") == "Deploy Mailu via Helm":
                return task["kubernetes.core.helm"]["values"]
    raise AssertionError("Mailu Helm task not found")


def setting(conf, name):
    match = re.search(rf"^\s*{name}\s*=\s*(.*)$", conf, re.M)
    return match.group(1).split() if match else None


class MailuMainValues(unittest.TestCase):
    def test_redis_admits_only_the_mailu_namespace(self):
        # allowExternal false alone admits only pods with the Redis client
        # label; the Mailu pods do not have it.
        policy = main_values()["redis"]["networkPolicy"]
        self.assertIs(policy["allowExternal"], False)
        self.assertEqual(policy["ingressNSMatchLabels"],
                         {"kubernetes.io/metadata.name": "{{ mailu.namespace }}"})

    def test_personal_sieve_scripts_cannot_use_editheader(self):
        conf = main_values()["dovecot"]["overrides"]["dovecot.conf"]
        self.assertRegex(conf, r"^plugin \{\n(?:.*\n)*\}\n$")
        self.assertEqual(setting(conf, "sieve_extensions"), ["+spamtest", "+spamtestplus"])
        # The global sieve_before script of Mailu needs editheader.
        self.assertEqual(setting(conf, "sieve_global_extensions"),
                         ["+vnd.dovecot.execute", "+editheader"])


if __name__ == "__main__":
    unittest.main()
