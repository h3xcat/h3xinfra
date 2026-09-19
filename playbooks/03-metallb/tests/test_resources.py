"""Verify native MetalLB budgets and explicitly identify upstream init gaps."""

import copy
import os
from pathlib import Path
import subprocess
import unittest

import yaml


VALUES = Path(__file__).resolve().parents[1] / "metallb-values.yaml"
CHART = os.environ.get("METALLB_CHART_PATH")
PATHS = {
    "controller.resources": ("100m", "256Mi", "1", "512Mi", "256Mi"),
    "speaker.resources": ("100m", "128Mi", "1", "512Mi", "256Mi"),
    "frr-k8s.frrk8s.resources": ("100m", "256Mi", "1", "512Mi", "256Mi"),
    "frr-k8s.frrk8s.frr.resources": ("50m", "64Mi", "500m", "256Mi", "1Gi"),
    "frr-k8s.frrk8s.frrMetrics.resources": ("50m", "64Mi", "500m", "256Mi", "256Mi"),
    "frr-k8s.frrk8s.frrStatus.resources": ("25m", "64Mi", "250m", "256Mi", "256Mi"),
    "frr-k8s.frrk8s.reloader.resources": ("10m", "16Mi", "100m", "64Mi", "256Mi"),
}
UNSUPPORTED_INIT = {"cp-frr-files", "cp-reloader", "cp-metrics", "cp-frr-status"}


def get_path(value, path):
    for key in path.split("."):
        value = value[key]
    return value


def remove_path(value, path):
    keys = path.split(".")
    parents = [value]
    for key in keys[:-1]:
        parents.append(parents[-1][key])
    del parents[-1][keys[-1]]
    for index in range(len(keys) - 2, -1, -1):
        if not parents[index + 1]:
            del parents[index][keys[index]]


def normalized(documents):
    result = {}
    for original in documents:
        doc = copy.deepcopy(original)
        if doc["kind"] in {"Deployment", "DaemonSet", "StatefulSet", "Job"}:
            spec = doc["spec"]["template"]["spec"]
            for container in spec.get("containers", []) + spec.get("initContainers", []):
                container.pop("resources", None)
        result[(doc["kind"], doc["metadata"]["name"])] = doc
    return result


class MetalLBBudgetDefaultsTests(unittest.TestCase):
    def test_approved_cpu_memory_and_ephemeral_budgets(self):
        values = yaml.safe_load(VALUES.read_text())
        for path, (cpu_request, memory_request, cpu_limit, memory_limit, ephemeral_limit) in PATHS.items():
            with self.subTest(path=path):
                self.assertEqual(get_path(values, path), {
                    "requests": {"cpu": cpu_request, "memory": memory_request, "ephemeral-storage": "32Mi"},
                    "limits": {"cpu": cpu_limit, "memory": memory_limit, "ephemeral-storage": ephemeral_limit},
                })

    def test_no_unsupported_init_keys_or_backend_changes(self):
        values = yaml.safe_load(VALUES.read_text())
        self.assertFalse(values["speaker"]["frr"]["enabled"])
        self.assertNotIn("initContainers", values["frr-k8s"]["frrk8s"])
        self.assertNotIn("frrk8s", values)


@unittest.skipUnless(CHART, "Set METALLB_CHART_PATH to a local MetalLB 0.16.1 chart")
class MetalLBResourceRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        metadata = yaml.safe_load((Path(CHART) / "Chart.yaml").read_text())
        if metadata["version"] != "0.16.1":
            raise AssertionError("Expected exact MetalLB chart 0.16.1")
        values = yaml.safe_load(VALUES.read_text())
        cls.after = cls.render(values)
        for path in PATHS:
            remove_path(values, path)
        cls.before = cls.render(values)

    @staticmethod
    def render(values):
        result = subprocess.run(
            ["helm", "template", "h3xinfra-metallb-main", CHART, "-n", "metallb-system",
             "--kube-version", "1.36.4", "-f", "-"],
            input=yaml.safe_dump(values), capture_output=True, text=True, check=True, timeout=60,
        )
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def test_all_regular_containers_and_only_known_init_gaps(self):
        regular = 0
        gaps = set()
        for document in self.after:
            if document["kind"] not in {"Deployment", "DaemonSet", "StatefulSet", "Job"}:
                continue
            spec = document["spec"]["template"]["spec"]
            for container in spec.get("containers", []):
                regular += 1
                with self.subTest(workload=document["metadata"]["name"], container=container["name"]):
                    for category in ("requests", "limits"):
                        for resource in ("cpu", "memory", "ephemeral-storage"):
                            self.assertTrue(container.get("resources", {}).get(category, {}).get(resource))
            for container in spec.get("initContainers", []):
                self.assertEqual(document["metadata"]["name"], "h3xinfra-metallb-main-frr-k8s")
                self.assertFalse(container.get("resources"))
                gaps.add(container["name"])
        self.assertEqual(regular, 8)
        self.assertEqual(gaps, UNSUPPORTED_INIT)

    def test_statuscleaner_inherits_the_controller_budget(self):
        cleaner = next(doc for doc in self.after
                       if doc["metadata"]["name"] == "h3xinfra-metallb-main-frr-k8s-statuscleaner")
        resources = cleaner["spec"]["template"]["spec"]["containers"][0]["resources"]
        values = yaml.safe_load(VALUES.read_text())
        self.assertEqual(resources, values["frr-k8s"]["frrk8s"]["resources"])

    def test_only_resources_change(self):
        self.assertEqual(normalized(self.before), normalized(self.after))
        for doc in self.after:
            if doc["kind"] == "DaemonSet":
                strategy = doc["spec"].get("updateStrategy", {})
                self.assertEqual(strategy.get("type"), "RollingUpdate")
                self.assertEqual(strategy.get("rollingUpdate", {}).get("maxUnavailable", 1), 1)


if __name__ == "__main__":
    unittest.main()
