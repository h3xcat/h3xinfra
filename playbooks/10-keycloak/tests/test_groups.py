"""Test mocked Keycloak client/group plans and the groups tfvars pass-through."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import jinja2
import yaml


LAYER = Path(__file__).resolve().parents[1]
TERRAFORM = os.environ.get("TERRAFORM") or shutil.which("terraform") or "terraform"
# Only the client/group/scope module files. No backend, state, tfvars or real
# provider calls can enter this test.
MODULE_FILES = ("providers.tf", "variables.tf", "realm.tf", "clients.tf", "groups.tf",
                "kubernetes_oidc.tf", "oidc-moved.tf", ".terraform.lock.hcl")


class KeycloakGroupsTests(unittest.TestCase):
    def test_terraform_contracts(self):
        source = LAYER / "terraform"
        with tempfile.TemporaryDirectory(prefix="h3x-keycloak-groups-") as directory:
            target = Path(directory)
            for name in MODULE_FILES:
                shutil.copyfile(source / name, target / name)
            (target / "tests").mkdir()
            for test in sorted((LAYER / "tests").glob("*.tftest.hcl")):
                shutil.copyfile(test, target / "tests" / test.name)
            env = dict(os.environ, TF_DATA_DIR=str(target / ".terraform"), TF_IN_AUTOMATION="1", TF_INPUT="0")
            for arguments in (["init", "-backend=false", "-lockfile=readonly"], ["test"]):
                result = subprocess.run([TERRAFORM, *arguments, "-no-color"], cwd=target, env=env,
                                        capture_output=True, text=True, timeout=300)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def render_tfvars(self, keycloak):
        plays = yaml.safe_load((LAYER / "standup.yml").read_text())
        tasks = [task for play in plays for task in play.get("tasks", [])]
        task = next(task for task in tasks if task.get("name") == "Render Terraform variables file")
        self.assertTrue(task.get("no_log"), "the tfvars task renders secrets and must set no_log")
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["to_nice_json"] = lambda value: json.dumps(value, indent=4, sort_keys=True)
        return json.loads(env.from_string(task["ansible.builtin.copy"]["content"]).render(keycloak=keycloak))

    def test_standup_passes_groups_to_terraform(self):
        keycloak = {
            "issuer_url_base": "https://keycloak.example.invalid",
            "admin": {"username": "admin", "password": "FAKE"},
            "realm": {"name": "test", "display_name": "Test", "smtp": {}},
        }
        self.assertEqual(self.render_tfvars(keycloak)["groups"], [])
        groups = [
            {"name": "report-editors", "client_roles": {"reports-cms": ["report-editor"]}},
            {"name": "report-publishers", "client_roles": {"reports-cms": ["report-publisher"]}, "members": ["alice"]},
        ]
        self.assertEqual(self.render_tfvars(dict(keycloak, groups=groups))["groups"], groups)

    def test_tfvars_tag_renders_without_apply(self):
        # The README import steps run standup.yml with --tags keycloak-tfvars.
        # That run must render terraform.tfvars.json and must not apply.
        plays = yaml.safe_load((LAYER / "standup.yml").read_text())
        tagged = []
        for play in plays:
            self.assertNotIn("keycloak-tfvars", play.get("tags", []), "tag the tasks, not a play")
            for task in play.get("tasks", []):
                tags = task.get("tags", [])
                tags = [tags] if isinstance(tags, str) else tags
                self.assertNotIn("always", tags)
                if "keycloak-tfvars" in tags:
                    tagged.append(task["name"])
                    self.assertNotIn("community.general.terraform", task)
        self.assertEqual(tagged, [
            "Ensure Terraform workspace directory exists",
            "Ensure Terraform state directory exists on NAS",
            "Render Terraform variables file",
        ])


if __name__ == "__main__":
    unittest.main()
