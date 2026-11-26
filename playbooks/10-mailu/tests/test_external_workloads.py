"""Check the mailu.external_workloads flag of standup.yml, teardown.yml and the stack."""
from pathlib import Path
import unittest

import jinja2
import yaml

LAYER = Path(__file__).resolve().parents[1]
STACK = LAYER.parent / "stack-standup.yml"
# The tasks run only when the flag is off. Unset means off.
FLAG_OFF = "not (mailu.external_workloads | default(false) | bool)"


def flagged(playbook):
    """Return {task name: when} for each task that names the flag."""
    tasks = [t for play in yaml.safe_load((LAYER / playbook).read_text())
             for t in play.get("tasks", [])]
    return {t["name"]: t.get("when") for t in tasks if "external_workloads" in yaml.safe_dump(t)}


def runs(context):
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.filters["bool"] = bool
    return env.compile_expression(FLAG_OFF)(**context)


class ExternalWorkloads(unittest.TestCase):
    def test_standup_skips_only_the_namespace_and_the_main_release(self):
        # The pre release, the address checks and the plugin tasks still run.
        self.assertEqual(flagged("standup.yml"), {
            "Create Mailu namespace": FLAG_OFF,
            "Deploy Mailu via Helm": FLAG_OFF,
        })

    def test_teardown_removes_only_the_pre_release(self):
        self.assertEqual(flagged("teardown.yml"), {
            "Uninstall Mailu via Helm": FLAG_OFF,
            "Remove Mailu namespace": FLAG_OFF,
        })

    def test_stack_skips_the_layer_with_the_flag(self):
        imports = {p["import_playbook"]: p.get("when") for p in yaml.safe_load(STACK.read_text())}
        self.assertEqual(imports["10-mailu/standup.yml"], FLAG_OFF)
        self.assertEqual([k for k, v in imports.items() if v == FLAG_OFF], ["10-mailu/standup.yml"])

    def test_flag_defaults_to_off_also_on_localhost(self):
        # The import condition also applies to the kube-connect play on
        # localhost, which has no `mailu` variable.
        self.assertTrue(runs({}))
        self.assertTrue(runs({"mailu": {"namespace": "mailu"}}))
        self.assertTrue(runs({"mailu": {"external_workloads": False}}))
        self.assertFalse(runs({"mailu": {"external_workloads": True}}))


if __name__ == "__main__":
    unittest.main()
