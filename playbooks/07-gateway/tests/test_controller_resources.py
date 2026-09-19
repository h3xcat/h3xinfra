"""Check controller budgets and the supported EnvoyProxy resource patch."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


PLAYBOOKS = Path(__file__).resolve().parents[2]
CHART = PLAYBOOKS / "07-gateway/charts/h3xinfra-gateway-pre"
CHART_ROOT = os.environ.get("PLATFORM_CHARTS_DIR")
HELM = os.environ.get("HELM") or shutil.which("helm") or "helm"


def budget(cpu_request, memory_request, cpu_limit, memory_limit, disk_limit="256Mi"):
    return {
        "requests": {
            "cpu": cpu_request, "memory": memory_request, "ephemeral-storage": "32Mi",
        },
        "limits": {
            "cpu": cpu_limit, "memory": memory_limit, "ephemeral-storage": disk_limit,
        },
    }


CONTROL = budget("50m", "128Mi", "1", "512Mi")
WEBHOOK = budget("25m", "64Mi", "500m", "256Mi")
HOOK = budget("50m", "64Mi", "500m", "256Mi")
DNS = budget("25m", "128Mi", "500m", "512Mi")
GATEWAY = budget("100m", "256Mi", "1", "1Gi")
ENVOY = budget("100m", "128Mi", "2", "512Mi", "1Gi")
SHUTDOWN = budget("25m", "128Mi", "500m", "256Mi")


def helm_values(layer, task_name):
    for play in yaml.safe_load((PLAYBOOKS / layer / "standup.yml").read_text()):
        for task in play.get("tasks", []):
            if task.get("name") == task_name:
                return copy.deepcopy(task["kubernetes.core.helm"]["values"])
    raise AssertionError(f"Helm task not found: {task_name}")


def render(chart, values):
    result = subprocess.run(
        [HELM, "template", "test-controller", str(chart), "--namespace", "test-ns", "-f", "-"],
        input=yaml.safe_dump(values), capture_output=True, text=True, check=True, timeout=120,
    )
    return [item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict)]


def containers(documents):
    result = {}
    for document in documents:
        if document.get("kind") not in {"Deployment", "DaemonSet", "StatefulSet", "Job"}:
            continue
        spec = document["spec"]["template"]["spec"]
        for container in spec.get("containers", []) + spec.get("initContainers", []):
            if container["name"] in result:
                raise AssertionError(f"Repeated container name: {container['name']}")
            result[container["name"]] = container["resources"]
    return result


class ControllerResourceTests(unittest.TestCase):
    def test_certificate_budgets_preserve_ha(self):
        values = helm_values("05-certmanager", "Deploy Cert-Manager via Helm")
        self.assertEqual(values["resources"], CONTROL)
        self.assertEqual(values["cainjector"]["resources"], CONTROL)
        self.assertEqual(values["webhook"]["resources"], WEBHOOK)
        self.assertEqual(values["startupapicheck"]["resources"], HOOK)
        for component in (values, values["webhook"], values["cainjector"]):
            self.assertEqual(component["replicaCount"], 2)
            self.assertEqual(component["podDisruptionBudget"], {"enabled": True, "minAvailable": 1})

    def test_external_dns_and_gateway_budgets(self):
        dns = helm_values("06-externaldns", "Deploy ExternalDNS via Helm")
        self.assertEqual(dns["resources"], DNS)
        gateway = helm_values("07-gateway", "Deploy Envoy Gateway controller via Helm (OCI)")
        self.assertEqual(gateway["deployment"]["replicas"], 2)
        self.assertEqual(gateway["deployment"]["envoyGateway"]["resources"], GATEWAY)
        self.assertEqual(gateway["certgen"]["job"]["resources"], HOOK)

    def test_envoy_proxy_native_fields_and_sidecar_patch(self):
        documents = render(CHART, {
            "wildcardCertificate": {"commonName": "example.invalid", "dnsNames": ["example.invalid"]},
            "ipPool": {"ipAddressPools": {"addresses": {"ipv4": "192.0.2.10/32"}}},
        })
        proxy = next(doc for doc in documents if doc["kind"] == "EnvoyProxy")
        self.assertEqual(proxy["spec"]["ipFamily"], "DualStack")
        deployment = proxy["spec"]["provider"]["kubernetes"]["envoyDeployment"]
        self.assertEqual(deployment["replicas"], 2)
        self.assertEqual(deployment["container"]["resources"], ENVOY)
        self.assertEqual(deployment["patch"], {
            "type": "StrategicMerge",
            "value": {"spec": {"template": {"spec": {"containers": [
                {"name": "shutdown-manager", "resources": SHUTDOWN},
            ]}}}},
        })
        self.assertTrue(deployment["pod"]["affinity"]["podAntiAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"])

    @unittest.skipUnless(shutil.which("kubectl"), "kubectl needed for local StrategicMerge verification")
    def test_shutdown_patch_preserves_other_container_fields(self):
        documents = render(CHART, {
            "wildcardCertificate": {"commonName": "example.invalid", "dnsNames": ["example.invalid"]},
            "ipPool": {"ipAddressPools": {"addresses": {"ipv4": "192.0.2.10/32"}}},
        })
        proxy = next(doc for doc in documents if doc["kind"] == "EnvoyProxy")
        patch = proxy["spec"]["provider"]["kubernetes"]["envoyDeployment"]["patch"]["value"]
        before = {
            "apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "test-proxy"},
            "spec": {"replicas": 2, "selector": {"matchLabels": {"app": "test"}}, "template": {
                "metadata": {"labels": {"app": "test"}}, "spec": {"containers": [
                    {"name": "envoy", "image": "example.invalid/envoy:test", "resources": ENVOY},
                    {"name": "shutdown-manager", "image": "example.invalid/shutdown:test", "args": ["serve"]},
                ]},
            }},
        }
        result = subprocess.run(
            ["kubectl", "patch", "--local", "--type", "strategic", "-f", "-", "-p", json.dumps(patch), "-o", "json"],
            input=json.dumps(before), capture_output=True, text=True, check=True, timeout=30,
        )
        expected = copy.deepcopy(before)
        expected["spec"]["template"]["spec"]["containers"][1]["resources"] = SHUTDOWN
        self.assertEqual(json.loads(result.stdout), expected)


@unittest.skipUnless(CHART_ROOT, "Set PLATFORM_CHARTS_DIR to local pinned charts")
class ControllerChartRenderTests(unittest.TestCase):
    def test_certificate_containers_and_startup_hook(self):
        values = helm_values("05-certmanager", "Deploy Cert-Manager via Helm")
        actual = containers(render(Path(CHART_ROOT) / "cert-manager", values))
        self.assertEqual(actual, {
            "cert-manager-controller": CONTROL,
            "cert-manager-cainjector": CONTROL,
            "cert-manager-webhook": WEBHOOK,
            "cert-manager-startupapicheck": HOOK,
        })

    def test_external_dns_container(self):
        values = helm_values("06-externaldns", "Deploy ExternalDNS via Helm")
        values["env"][0]["valueFrom"]["secretKeyRef"]["name"] = "test-only-token"
        self.assertEqual(containers(render(Path(CHART_ROOT) / "external-dns", values)), {"external-dns": DNS})

    def test_gateway_controller_and_certgen_hook(self):
        values = helm_values("07-gateway", "Deploy Envoy Gateway controller via Helm (OCI)")
        self.assertEqual(containers(render(Path(CHART_ROOT) / "gateway-helm", values)), {
            "envoy-gateway": GATEWAY, "envoy-gateway-certgen": HOOK,
        })


if __name__ == "__main__":
    unittest.main()
