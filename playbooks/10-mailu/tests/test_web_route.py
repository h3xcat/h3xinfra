"""Render the Mailu web routes with synthetic values."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


LAYER = Path(__file__).resolve().parents[1]
CHART = LAYER / "charts" / "h3xinfra-mailu-pre"
DNS_NAMES = ["mail.app.example.test", "mail.example.test"]


def render(**route):
    values = {
        "certificate": {"commonName": DNS_NAMES[0], "dnsNames": DNS_NAMES},
        "gatewayRoute": {
            "enabled": True, "gateway": {"name": "shared", "namespace": "gateway"},
            "oidc": {"enabled": True, "clientSecret": "synthetic", "secretRef": {"name": "oidc"}},
            "oidcHostname": "mail.example.test",
            "publicPaths": ["/.well-known/", "/autoconfig"],
            **route,
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "values.json"
        path.write_text(json.dumps(values))
        result = subprocess.run(
            ["helm", "template", "test", str(CHART), "-n", "mailu", "-f", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    if result.returncode:
        raise AssertionError(result.stderr)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def route_hostnames(docs):
    return {d["metadata"]["name"]: d["spec"]["hostnames"] for d in docs if d["kind"] == "HTTPRoute"}


@unittest.skipUnless(shutil.which("helm"), "helm is not installed")
class MailuWebRouteTests(unittest.TestCase):
    def test_routes_default_to_every_certificate_name(self):
        self.assertEqual(route_hostnames(render()), {
            "test-h3xinfra-mailu-pre-web": DNS_NAMES,
            "test-h3xinfra-mailu-pre-web-public": DNS_NAMES,
        })

    def test_web_hostnames_limit_both_routes(self):
        docs = render(hostnames=["mail.example.test"])
        self.assertEqual(route_hostnames(docs), {
            "test-h3xinfra-mailu-pre-web": ["mail.example.test"],
            "test-h3xinfra-mailu-pre-web-public": ["mail.example.test"],
        })
        certificate = next(d for d in docs if d["kind"] == "Certificate")
        self.assertEqual(certificate["spec"]["dnsNames"], DNS_NAMES)

    def test_oidc_host_outside_the_routes_fails(self):
        with self.assertRaisesRegex(AssertionError, "OIDC callback host"):
            render(hostnames=["mail.app.example.test"])

    def test_common_name_callback_outside_the_routes_fails(self):
        with self.assertRaisesRegex(AssertionError, "OIDC callback host"):
            render(hostnames=["mail.example.test"], oidcHostname="")

    def test_common_name_callback_inside_the_routes_renders(self):
        docs = render(hostnames=["mail.app.example.test"], oidcHostname="")
        policy = next(d for d in docs if d["kind"] == "SecurityPolicy")
        self.assertEqual(policy["spec"]["oidc"]["redirectURL"],
                         "https://mail.app.example.test/oauth2/callback")

    def test_standup_passes_inventory_web_hostnames(self):
        for play in yaml.safe_load((LAYER / "standup.yml").read_text()):
            for task in play.get("tasks", []):
                if task.get("name") == "Deploy Mailu configuration via Helm":
                    route = task["kubernetes.core.helm"]["values"]["gatewayRoute"]
                    self.assertEqual(route["hostnames"], "{{ mailu.web_hostnames | default([]) }}")
                    return
        self.fail("Mailu pre-chart Helm task not found")


if __name__ == "__main__":
    unittest.main()
