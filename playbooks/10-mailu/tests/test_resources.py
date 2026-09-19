"""Check Mailu budgets and optionally render the pinned upstream chart offline."""

import copy
import os
from pathlib import Path
import subprocess
import unittest

from jinja2 import ChainableUndefined, Environment
import yaml


PLAYBOOK = Path(__file__).resolve().parents[1] / "standup.yml"
CHART = os.environ.get("MAILU_CHART_PATH")
EXPECTED = {
    "admin": ("100m", "256Mi", "1", "768Mi"),
    "clamav": ("100m", "1536Mi", "2", "4Gi"),
    "dovecot": ("100m", "256Mi", "1", "1Gi"),
    "front": ("100m", "384Mi", "1", "1Gi"),
    "oletools": ("50m", "64Mi", "500m", "256Mi"),
    "postfix": ("100m", "128Mi", "1", "512Mi"),
    "rspamd": ("100m", "384Mi", "2", "1Gi"),
    "tika": ("100m", "768Mi", "2", "2Gi"),
    "webmail": ("100m", "192Mi", "1", "512Mi"),
}


def helm_values():
    for play in yaml.safe_load(PLAYBOOK.read_text()):
        for task in play.get("tasks", []):
            if task.get("name") == "Deploy Mailu via Helm":
                return task["kubernetes.core.helm"]["values"]
    raise AssertionError("Mailu Helm task not found")


def budgets(overrides=None):
    env = Environment(undefined=ChainableUndefined)
    values = helm_values()
    return {
        component: {
            category: {
                resource: env.from_string(expression).render(
                    mailu={"resources": overrides or {}}
                )
                for resource, expression in entries.items()
            }
            for category, entries in values[component]["resources"].items()
        }
        for component in EXPECTED
    }


class MailuResourcesTests(unittest.TestCase):
    def test_measured_defaults_cover_all_enabled_mailu_components(self):
        actual = budgets()
        for component, (cpu_request, memory_request, cpu_limit, memory_limit) in EXPECTED.items():
            with self.subTest(component=component):
                self.assertEqual(actual[component], {
                    "requests": {"cpu": cpu_request, "memory": memory_request},
                    "limits": {"cpu": cpu_limit, "memory": memory_limit},
                })

    def test_inventory_override_preserves_other_defaults(self):
        expected = budgets()
        expected["clamav"]["limits"]["memory"] = "5Gi"
        self.assertEqual(budgets({"clamav": {"limits": {"memory": "5Gi"}}}), expected)

    def test_existing_database_enablement_and_redis_budget_are_untouched(self):
        values = helm_values()
        for component in ("mariadb", "postgresql"):
            self.assertNotIn("enabled", values[component])
        self.assertNotIn("resources", values["redis"]["master"])
        self.assertNotIn("resourcesPreset", values["redis"]["master"])


@unittest.skipUnless(CHART, "Set MAILU_CHART_PATH to a local Mailu 2.7.3 chart")
class MailuChartRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        chart_meta = yaml.safe_load((Path(CHART) / "Chart.yaml").read_text())
        if chart_meta["version"] != "2.7.3":
            raise AssertionError("Render verification requires exact Mailu chart 2.7.3")
        fixture = {
            "domain": "example.invalid",
            "hostnames": ["mail.example.invalid"],
            "secretKey": "test-only-stable-mailu-secret-key",
            "initialAccount": {"password": "test-only-admin-password"},
            "global": {"database": {"roundcube": {"password": "test-only-password"}}},
            "persistence": {"single_pvc": False},
            "ingress": {"enabled": False},
        }
        cls.before = cls.render(fixture)
        candidate = copy.deepcopy(fixture)
        for component, resources in budgets().items():
            candidate[component] = {"resources": resources}
        cls.after = cls.render(candidate)

    @staticmethod
    def render(values):
        result = subprocess.run(
            ["helm", "template", "h3xinfra-mailu-main", CHART, "--namespace", "mailu", "-f", "-"],
            input=yaml.safe_dump(values), capture_output=True, text=True, check=True, timeout=60,
        )
        return {
            (doc["kind"], doc["metadata"]["name"]): doc
            for doc in yaml.safe_load_all(result.stdout) if doc
        }

    def test_all_rendered_regular_and_init_containers_are_bounded(self):
        seen = set()
        for doc in self.after.values():
            if doc["kind"] not in {"Deployment", "StatefulSet", "DaemonSet", "Job"}:
                continue
            spec = doc["spec"]["template"]["spec"]
            for container in spec.get("containers", []) + spec.get("initContainers", []):
                name = container["name"]
                with self.subTest(workload=doc["metadata"]["name"], container=name):
                    resources = container.get("resources", {})
                    for category in ("requests", "limits"):
                        for resource in ("cpu", "memory"):
                            self.assertTrue(resources.get(category, {}).get(resource))
                    if name in EXPECTED:
                        seen.add(name)
                        self.assertEqual(resources, budgets()[name])
        self.assertEqual(seen, set(EXPECTED))

    def test_only_the_nine_resource_blocks_change(self):
        after = copy.deepcopy(self.after)
        changed = []
        for key, doc in after.items():
            if doc["kind"] not in {"Deployment", "StatefulSet"}:
                continue
            previous = self.before[key]["spec"]["template"]["spec"]["containers"]
            for index, container in enumerate(doc["spec"]["template"]["spec"]["containers"]):
                if container["name"] in EXPECTED:
                    changed.append(container["name"])
                    container["resources"] = previous[index]["resources"]
        self.assertCountEqual(changed, EXPECTED)
        self.assertEqual(after, self.before)


if __name__ == "__main__":
    unittest.main()
