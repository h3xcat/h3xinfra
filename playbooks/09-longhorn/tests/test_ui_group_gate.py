"""The optional Keycloak group gate of the Longhorn UI route."""

import json
import os
from pathlib import Path
import subprocess
import unittest

import yaml


CHART = Path(__file__).resolve().parents[1] / "charts/h3xinfra-longhorn-post"
PLAYBOOK = Path(__file__).resolve().parents[1] / "standup.yml"
ISSUER = "https://auth.example.com/realms/h3xinfra"
INGRESS = {
    "host": "longhorn.example.com",
    "gateway": {"name": "shared-gateway", "namespace": "gateway-system"},
    "trustedIPs": ["0.0.0.0/0", "::/0"],
    "oidc": {
        "enabled": True,
        "issuerUrl": ISSUER,
        "clientId": "longhorn",
        "clientSecret": "FAKE",
        "secretRef": {"name": "longhorn-oidc"},
    },
}


def render(**oidc):
    ingress = json.loads(json.dumps(INGRESS))
    ingress["oidc"].update(oidc)
    return subprocess.run(
        [os.environ.get("HELM", "helm"), "template", "test-longhorn", str(CHART),
         "-n", "longhorn-system", "-f", "-"],
        input=json.dumps({"longhornIngress": ingress}), capture_output=True, text=True,
        timeout=30,
    )


def kinds(result):
    return {doc["kind"]: doc for doc in yaml.safe_load_all(result.stdout)
            if isinstance(doc, dict) and doc.get("kind") in ("SecurityPolicy", "HTTPRoute")}


class UiGroupGateTests(unittest.TestCase):
    def test_group_gate(self):
        result = render(allowedGroups=["k8s-admins"])
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = kinds(result)
        spec = docs["SecurityPolicy"]["spec"]
        self.assertEqual(spec["authorization"], {"defaultAction": "Deny", "rules": [{
            "action": "Allow", "principal": {
                "clientCIDRs": ["0.0.0.0/0", "::/0"],
                "jwt": {
                    "provider": "keycloak-id-token",
                    "claims": [{"name": "groups", "valueType": "StringArray", "values": ["k8s-admins"]}],
                },
            },
        }]})
        self.assertEqual(spec["jwt"]["providers"], [{
            "name": "keycloak-id-token",
            "issuer": ISSUER,
            "audiences": ["longhorn"],
            "remoteJWKS": {"uri": ISSUER + "/protocol/openid-connect/certs"},
            "extractFrom": {"headers": [{"name": "X-Oidc-Id-Token"}]},
        }])
        self.assertEqual(spec["oidc"]["forwardIDToken"], {"header": "X-Oidc-Id-Token"})
        rule = docs["HTTPRoute"]["spec"]["rules"][0]
        self.assertEqual(rule["filters"], [{"type": "RequestHeaderModifier",
                                            "requestHeaderModifier": {"remove": ["X-Oidc-Id-Token"]}}])

    def test_no_groups_keeps_the_plain_oidc_policy(self):
        result = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = kinds(result)
        spec = docs["SecurityPolicy"]["spec"]
        self.assertNotIn("jwt", spec)
        self.assertNotIn("forwardIDToken", spec["oidc"])
        self.assertEqual(spec["authorization"]["rules"][0]["principal"],
                         {"clientCIDRs": ["0.0.0.0/0", "::/0"]})
        self.assertNotIn("filters", docs["HTTPRoute"]["spec"]["rules"][0])

    def test_groups_without_oidc_fail(self):
        result = render(enabled=False, allowedGroups=["k8s-admins"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("allowedGroups needs oidc.enabled", result.stderr)

    def test_standup_passes_the_groups_to_the_post_chart(self):
        tasks = [task for play in yaml.safe_load(PLAYBOOK.read_text()) for task in play.get("tasks", [])]
        post = next(task for task in tasks if task["name"] == "Deploy Longhorn configuration via Helm")
        oidc = post["kubernetes.core.helm"]["values"]["longhornIngress"]["oidc"]
        self.assertEqual(oidc["allowedGroups"],
                         "{{ longhorn.auth.oauth2_proxy.allowed_groups | default([]) }}")


if __name__ == "__main__":
    unittest.main()
