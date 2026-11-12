"""Availability guards; the shared private Helm suite checks resource budgets."""

import os
from pathlib import Path
import subprocess
import unittest

import yaml


PLAYBOOKS = Path(__file__).resolve().parents[2]


def helm_values(layer, task_name):
    for play in yaml.safe_load((PLAYBOOKS / layer / "standup.yml").read_text()):
        for task in play.get("tasks", []):
            if task.get("name") == task_name:
                return task["kubernetes.core.helm"]["values"]
    raise AssertionError(f"Helm task not found: {task_name}")


class ControllerAvailabilityTests(unittest.TestCase):
    def test_certificate_controllers_preserve_replicas_and_disruption_budgets(self):
        values = helm_values("05-certmanager", "Deploy Cert-Manager via Helm")
        for component in (values, values["webhook"], values["cainjector"]):
            self.assertEqual(component["replicaCount"], 2)
            self.assertEqual(component["podDisruptionBudget"], {"enabled": True, "minAvailable": 1})

    def test_gateway_controller_has_two_replicas(self):
        values = helm_values("07-gateway", "Deploy Envoy Gateway controller via Helm (OCI)")
        self.assertEqual(values["deployment"]["replicas"], 2)

    def test_envoy_proxy_is_dual_stack_and_spans_nodes(self):
        values = {
            "wildcardCertificate": {"commonName": "example.invalid", "dnsNames": ["example.invalid"]},
            "ipPool": {"ipAddressPools": {"addresses": {"ipv4": "192.0.2.10/32"}}},
        }
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", "test-controller",
             str(PLAYBOOKS / "07-gateway/charts/h3xinfra-gateway-pre"), "--namespace", "test-ns", "-f", "-"],
            input=yaml.safe_dump(values), capture_output=True, text=True, check=True, timeout=120,
        )
        proxy = next(doc for doc in yaml.safe_load_all(result.stdout) if doc and doc["kind"] == "EnvoyProxy")
        self.assertEqual(proxy["spec"]["ipFamily"], "DualStack")
        deployment = proxy["spec"]["provider"]["kubernetes"]["envoyDeployment"]
        self.assertEqual(deployment["replicas"], 2)
        self.assertTrue(deployment["pod"]["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"])


if __name__ == "__main__":
    unittest.main()
