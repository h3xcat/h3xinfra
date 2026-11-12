"""Render Keycloak's image pin and PostgreSQL availability contracts."""

import copy
import os
from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


CHARTS = Path(__file__).resolve().parents[1] / "charts"
HELM = os.environ.get("HELM") or shutil.which("helm") or "helm"
VALUES = {
    "pre": {"bootstrapSecret": {"adminPassword": "fake"},
            "postgres": {"password": "fake", "image": {"tag": "0.0.0"}}},
    "main": {"hostname": "auth.example.test", "image": {"tag": "0.0.0"},
             "bootstrapAdminSecretName": "admin",
             "database": {"host": "postgres-rw", "credentialsSecretName": "db"}},
}


def render(chart, values):
    return subprocess.run(
        [HELM, "template", "test", str(CHARTS / f"h3xinfra-keycloak-{chart}"),
         "--namespace", "test-ns", "-f", "-"],
        input=yaml.safe_dump(values), capture_output=True, text=True, timeout=120,
    )


@unittest.skipUnless(shutil.which(HELM), "helm needed to render the charts")
class KeycloakChartsTests(unittest.TestCase):
    def test_postgres_instances_require_distinct_nodes(self):
        for override in ({}, {"podAntiAffinityType": "preferred"}):
            with self.subTest(legacy_override=override):
                values = copy.deepcopy(VALUES["pre"])
                values["postgres"].update(override)
                result = render("pre", values)
                self.assertEqual(result.returncode, 0, result.stderr)
                cluster = next(doc for doc in yaml.safe_load_all(result.stdout)
                               if doc and doc.get("kind") == "Cluster")
                self.assertEqual(cluster["spec"]["instances"], 2)
                self.assertEqual(cluster["spec"]["affinity"], {
                    "podAntiAffinityType": "required",
                    "topologyKey": "kubernetes.io/hostname",
                })

    def test_image_tags_must_be_supplied(self):
        for chart, prefix, kind, field in [
            ("pre", "postgres.", "Cluster", "imageName"),
            ("main", "", "Keycloak", "image"),
        ]:
            values = copy.deepcopy(VALUES[chart])
            with self.subTest(chart=chart, tag="0.0.0"):
                result = render(chart, values)
                self.assertEqual(result.returncode, 0, result.stderr)
                resource = next(doc for doc in yaml.safe_load_all(result.stdout)
                                if doc and doc.get("kind") == kind)
                self.assertTrue(resource["spec"][field].endswith(":0.0.0"))
            image = values["postgres"]["image"] if chart == "pre" else values["image"]
            for tag in ({}, {"tag": ""}, {"tag": None}):
                with self.subTest(chart=chart, image=tag):
                    image.clear()
                    image.update(tag)
                    result = render(chart, values)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"{prefix}image.tag is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
