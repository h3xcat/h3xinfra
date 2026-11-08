"""Check the pod anti-affinity of the Keycloak CNPG Postgres cluster."""

import os
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


CHART = Path(__file__).resolve().parents[1] / "charts/h3xinfra-keycloak-pre"
HELM = os.environ.get("HELM") or shutil.which("helm") or "helm"
BASE = {"bootstrapSecret": {"adminPassword": "x"}, "postgres": {"password": "x"}}


def render(postgres=None):
    values = yaml.safe_load(yaml.safe_dump(BASE))
    values["postgres"].update(postgres or {})
    return subprocess.run(
        [HELM, "template", "test", str(CHART), "--namespace", "test-ns", "-f", "-"],
        input=yaml.safe_dump(values), capture_output=True, text=True, timeout=120,
    )


def cluster_affinity(result):
    for document in yaml.safe_load_all(result.stdout):
        if isinstance(document, dict) and document.get("kind") == "Cluster":
            return document["spec"]["affinity"]
    raise AssertionError("CNPG Cluster not rendered")


@unittest.skipUnless(shutil.which(HELM), "helm needed to render the chart")
class PostgresAffinityTests(unittest.TestCase):
    def test_default_requires_distinct_nodes(self):
        result = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cluster_affinity(result), {
            "podAntiAffinityType": "required",
            "topologyKey": "kubernetes.io/hostname",
        })

    def test_preferred_is_an_explicit_opt_out(self):
        result = render({"podAntiAffinityType": "preferred"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cluster_affinity(result)["podAntiAffinityType"], "preferred")

    def test_unknown_type_fails_render(self):
        result = render({"podAntiAffinityType": "soft"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("postgres.podAntiAffinityType must be preferred or required", result.stderr)


if __name__ == "__main__":
    unittest.main()
