"""Verify Cilium budgets and their exact-chart rendering without cluster access."""

import copy
import os
from pathlib import Path
import subprocess
import unittest

import yaml


VALUES = Path(__file__).resolve().parents[1] / "cilium-values.yaml"
CHART = os.environ.get("CILIUM_CHART_PATH")
PATHS = {
    "resources": ("250m", "2Gi", "2", "4Gi", "1Gi"),
    "initResources": ("100m", "64Mi", "1", "256Mi", "256Mi"),
    "cni.resources": ("100m", "64Mi", "1", "256Mi", "256Mi"),
    "envoy.resources": ("100m", "128Mi", "1", "512Mi", "256Mi"),
    "operator.resources": ("100m", "256Mi", "1", "1Gi", "256Mi"),
    "hubble.relay.resources": ("50m", "128Mi", "500m", "512Mi", "256Mi"),
    "hubble.ui.backend.resources": ("25m", "128Mi", "500m", "256Mi", "256Mi"),
    "hubble.ui.frontend.resources": ("25m", "32Mi", "250m", "128Mi", "256Mi"),
    "certgen.resources": ("50m", "64Mi", "500m", "256Mi", "256Mi"),
}


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


def pod_spec(document):
    kind = document["kind"]
    if kind == "CronJob":
        return document["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    if kind in {"Deployment", "DaemonSet", "StatefulSet", "Job"}:
        return document["spec"]["template"]["spec"]
    return None


def normalized(documents):
    result = {}
    for original in documents:
        doc = copy.deepcopy(original)
        spec = pod_spec(doc)
        if spec is not None:
            for container in spec.get("containers", []) + spec.get("initContainers", []):
                container.pop("resources", None)
        if doc["kind"] == "DaemonSet" and doc["metadata"]["name"] in {"cilium", "cilium-envoy"}:
            doc["spec"]["updateStrategy"]["rollingUpdate"].pop("maxUnavailable")
        # The chart names this immutable Job by a checksum including resources.
        if doc["kind"] == "Job" and doc["metadata"].get("labels", {}).get("k8s-app") == "hubble-generate-certs":
            doc["metadata"]["name"] = "hubble-generate-certs-RESOURCE-CHECKSUM"
        result[(doc["kind"], doc["metadata"]["name"])] = doc
    return result


class CiliumBudgetDefaultsTests(unittest.TestCase):
    def test_approved_cpu_memory_and_ephemeral_budgets(self):
        values = yaml.safe_load(VALUES.read_text())
        for path, (cpu_request, memory_request, cpu_limit, memory_limit, ephemeral_limit) in PATHS.items():
            with self.subTest(path=path):
                self.assertEqual(get_path(values, path), {
                    "requests": {"cpu": cpu_request, "memory": memory_request, "ephemeral-storage": "32Mi"},
                    "limits": {"cpu": cpu_limit, "memory": memory_limit, "ephemeral-storage": ephemeral_limit},
                })

    def test_agent_and_envoy_roll_one_node_at_a_time(self):
        values = yaml.safe_load(VALUES.read_text())
        for path in ("updateStrategy", "envoy.updateStrategy"):
            self.assertEqual(get_path(values, path), {
                "type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 1},
            })


@unittest.skipUnless(CHART, "Set CILIUM_CHART_PATH to a local Cilium 1.20.1 chart")
class CiliumResourceRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        metadata = yaml.safe_load((Path(CHART) / "Chart.yaml").read_text())
        if metadata["version"] != "1.20.1":
            raise AssertionError("Expected exact Cilium chart 1.20.1")
        values = yaml.safe_load(VALUES.read_text())
        cls.after = cls.render(values)
        for path in [*PATHS, "updateStrategy", "envoy.updateStrategy"]:
            remove_path(values, path)
        cls.before = cls.render(values)

    @staticmethod
    def render(values):
        result = subprocess.run(
            ["helm", "template", "h3xinfra-cilium-main", CHART, "-n", "kube-system",
             "--kube-version", "1.36.4", "-f", "-"],
            input=yaml.safe_dump(values), capture_output=True, text=True, check=True, timeout=60,
        )
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def test_every_regular_init_and_job_container_is_bounded(self):
        count = 0
        for document in self.after:
            spec = pod_spec(document)
            if spec is None:
                continue
            for container in spec.get("containers", []) + spec.get("initContainers", []):
                count += 1
                with self.subTest(workload=document["metadata"]["name"], container=container["name"]):
                    for category in ("requests", "limits"):
                        for resource in ("cpu", "memory", "ephemeral-storage"):
                            self.assertTrue(container.get("resources", {}).get(category, {}).get(resource))
        self.assertEqual(count, 14)

    def test_only_resources_serial_rollouts_and_certgen_job_name_change(self):
        self.assertEqual(normalized(self.before), normalized(self.after))
        old_jobs = {doc["metadata"]["name"] for doc in self.before if doc["kind"] == "Job"}
        new_jobs = {doc["metadata"]["name"] for doc in self.after if doc["kind"] == "Job"}
        self.assertEqual(len(old_jobs), 1)
        self.assertEqual(len(new_jobs), 1)
        self.assertNotEqual(old_jobs, new_jobs)
        for doc in self.after:
            if doc["kind"] == "DaemonSet":
                self.assertEqual(doc["spec"]["updateStrategy"]["rollingUpdate"]["maxUnavailable"], 1)


if __name__ == "__main__":
    unittest.main()
