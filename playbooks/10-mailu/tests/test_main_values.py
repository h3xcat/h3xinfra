"""Check the Mailu chart values in standup.yml for Redis, Tika and Sieve."""
from pathlib import Path
import re
import unittest

import yaml

LAYER = Path(__file__).resolve().parents[1]


def helm_task(name):
    for play in yaml.safe_load((LAYER / "standup.yml").read_text()):
        for task in play.get("tasks", []):
            if task.get("name") == name:
                return task["kubernetes.core.helm"]
    raise AssertionError(f"{name} task not found")


def main_values():
    return helm_task("Deploy Mailu via Helm")["values"]


def setting(conf, name):
    match = re.search(rf"^\s*{name}\s*=\s*(.*)$", conf, re.M)
    return match.group(1).split() if match else None


class MailuMainValues(unittest.TestCase):
    def test_mailu_uses_the_pre_chart_redis(self):
        # The bundled Bitnami Redis (bitnamilegacy, frozen at 8.2.1) is off.
        values = main_values()
        self.assertEqual(values["redis"], {"enabled": False})
        pre = helm_task("Deploy Mailu configuration via Helm")["values"]
        # The Service of templates/redis.yaml is <pre-chart fullname>-redis.
        self.assertEqual(values["externalRedis"], {
            "enabled": True,
            "host": pre["fullnameOverride"] + "-redis.{{ mailu.namespace }}.svc.cluster.local",
            # REDIS_ADDRESS carries no port: Rspamd and the admin sessions
            # always use 6379.
            "port": 6379,
            # The database ids of the bundled Redis, so the data keeps its place.
            "adminQuotaDbId": 1,
            "adminRateLimitDbId": 2,
        })
        self.assertEqual(pre["redis"], {"image": {"tag": "{{ image_versions.mailu_redis }}"}})

    def test_tika_image_comes_from_the_inventory_pin(self):
        # Without the tag the chart default returns, which Renovate cannot see.
        self.assertEqual(main_values()["tika"]["image"],
                         {"tag": "{{ image_versions.mailu_tika }}"})

    def test_personal_sieve_scripts_cannot_use_editheader(self):
        conf = main_values()["dovecot"]["overrides"]["dovecot.conf"]
        self.assertRegex(conf, r"^plugin \{\n(?:.*\n)*\}\n$")
        self.assertEqual(setting(conf, "sieve_extensions"), ["+spamtest", "+spamtestplus"])
        # The global sieve_before script of Mailu needs editheader.
        self.assertEqual(setting(conf, "sieve_global_extensions"),
                         ["+vnd.dovecot.execute", "+editheader"])


if __name__ == "__main__":
    unittest.main()
