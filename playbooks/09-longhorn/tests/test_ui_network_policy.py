"""Only the shared Gateway's Envoy proxies may reach the Longhorn UI pods."""

import json
import os
from pathlib import Path
import subprocess
import unittest

import yaml


CHART = Path(__file__).resolve().parents[1] / "charts/h3xinfra-longhorn-post"
PLAYBOOK = Path(__file__).resolve().parents[1] / "standup.yml"
GATEWAY = {"name": "shared-gateway", "namespace": "gateway-system"}


def ui_policies(ingress):
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", "test-longhorn", str(CHART),
         "-n", "longhorn-system", "-f", "-"],
        input=json.dumps({"longhornIngress": ingress}), capture_output=True, text=True,
        check=True, timeout=30,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout)
            if isinstance(doc, dict) and doc.get("kind") == "NetworkPolicy"
            and doc["spec"]["podSelector"] == {"matchLabels": {"app": "longhorn-ui"}}]


class UiNetworkPolicyTests(unittest.TestCase):
    def test_ui_admits_only_the_gateway_proxies_on_the_ui_port(self):
        policies = ui_policies({"gateway": GATEWAY})
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0]["metadata"]["namespace"], "longhorn-system")
        self.assertEqual(policies[0]["spec"], {
            "podSelector": {"matchLabels": {"app": "longhorn-ui"}},
            "policyTypes": ["Ingress"],
            "ingress": [{
                "from": [{
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "gateway-system"}},
                    "podSelector": {"matchLabels": {
                        "gateway.envoyproxy.io/owning-gateway-name": "shared-gateway",
                        "gateway.envoyproxy.io/owning-gateway-namespace": "gateway-system",
                    }},
                }],
                "ports": [{"protocol": "TCP", "port": 8000}],
            }],
        })

    def test_ui_admits_no_pod_without_a_gateway(self):
        for ingress in ({}, {"host": "", "gateway": {"name": "", "namespace": ""}}):
            with self.subTest(ingress=ingress):
                policies = ui_policies(ingress)
                self.assertEqual(len(policies), 1)
                self.assertEqual(policies[0]["spec"]["policyTypes"], ["Ingress"])
                self.assertNotIn("ingress", policies[0]["spec"])

    def test_gateway_name_without_namespace_fails_rendering(self):
        with self.assertRaises(subprocess.CalledProcessError):
            ui_policies({"gateway": {"name": "shared-gateway"}})

    def test_standup_passes_the_shared_gateway_to_the_post_chart(self):
        tasks = [task for play in yaml.safe_load(PLAYBOOK.read_text()) for task in play.get("tasks", [])]
        post = next(task for task in tasks if task["name"] == "Deploy Longhorn configuration via Helm")
        self.assertEqual(post["kubernetes.core.helm"]["values"]["longhornIngress"]["gateway"], {
            "name": "{{ gateway.release_name_prefix }}-pre-shared",
            "namespace": "{{ gateway.namespace }}",
        })


if __name__ == "__main__":
    unittest.main()
