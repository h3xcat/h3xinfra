"""Exercise teardown ordering and failure gates with in-memory Kubernetes results."""

import copy
from pathlib import Path
import unittest

from jinja2 import ChainableUndefined
from jinja2.nativetypes import NativeEnvironment
import yaml


LAYER = Path(__file__).resolve().parents[1]
LONGHORN_CRD = {"spec": {"group": "longhorn.io"}}
OTHER_CRD = {"spec": {"group": "example.longhorn.io"}}


class TeardownStopped(Exception):
    pass


class TeardownFixture:
    """Evaluate actual task conditions; every external module is an in-memory fake."""

    def __init__(self, state="deployed", flag="false", crds=None, check=False,
                 fail=None, retain_crds=False):
        self.play = yaml.safe_load((LAYER / "teardown.yml").read_text())[2]
        self.context = {
            "longhorn": {"release_name_prefix": "fixture-longhorn", "namespace": "fixture-storage"},
            "ansible_check_mode": check,
        }
        self.state = state
        self.flag = flag
        self.crds = copy.deepcopy([LONGHORN_CRD] if crds is None else crds)
        self.check = check
        self.fail = fail
        self.retain_crds = retain_crds
        self.events = []
        self.env = NativeEnvironment(undefined=ChainableUndefined)

    def render(self, value, context):
        if isinstance(value, str):
            return self.env.from_string(value).render(context)
        if isinstance(value, list):
            return [self.render(item, context) for item in value]
        if isinstance(value, dict):
            return {key: self.render(item, context) for key, item in value.items()}
        return value

    def expression(self, value, context):
        return bool(self.env.compile_expression(value)(**context))

    def run(self, tasks=None):
        for task in self.play["tasks"] if tasks is None else tasks:
            context = self.context | self.render(task.get("vars", {}), self.context)
            conditions = task.get("when", [])
            if isinstance(conditions, str):
                conditions = [conditions]
            if not all(self.expression(condition, context) for condition in conditions):
                continue
            if "block" in task:
                self.run(task["block"])
                continue
            if "ansible.builtin.assert" in task:
                if not all(self.expression(item, context) for item in task["ansible.builtin.assert"]["that"]):
                    raise TeardownStopped(task["name"])
                continue
            module = next(key for key in task if key.startswith("kubernetes.core."))
            args = self.render(task[module], context)
            result = self.call(module, args)
            if "register" in task:
                self.context[task["register"]] = result
        return self.events

    def call(self, module, args):
        if module == "kubernetes.core.helm_info":
            return {} if self.state is None else {"status": {"status": self.state}}
        if module == "kubernetes.core.k8s_info":
            if args["kind"] == "CustomResourceDefinition":
                return {"api_found": True, "resources": copy.deepcopy(self.crds)}
            if args["kind"] == "Setting":
                return {"resources": [] if self.flag is None else [{"value": self.flag}]}
            raise AssertionError(args)
        if module == "kubernetes.core.k8s_json_patch":
            assert args == {
                "api_version": "longhorn.io/v1beta2", "kind": "Setting",
                "name": "deleting-confirmation-flag", "namespace": "fixture-storage",
                "patch": [{"op": "replace", "path": "/value", "value": "true"}],
            }, args
            event = "patch"
        elif module == "kubernetes.core.helm":
            assert args["state"] == "absent" and args["wait"] is True, args
            assert args["namespace"] == "fixture-storage", args
            event = args["name"].removeprefix("fixture-longhorn-")
            if event == "main":
                assert args.get("disable_hook") is False, args
                assert int(args["timeout"].removesuffix("s")) >= 900, args
        elif module == "kubernetes.core.k8s":
            assert args["state"] == "absent" and args["kind"] == "Namespace", args
            assert args["name"] == "fixture-storage" and args["wait"] is True, args
            event = "namespace"
        else:
            raise AssertionError(f"Unexpected external action: {module}")
        self.events.append(event)
        if self.fail == event:
            raise TeardownStopped(f"{event} failed")
        if not self.check:
            if event == "patch":
                self.flag = "true"
            elif event == "main" and self.state in ("deployed", "failed"):
                if self.flag != "true":
                    raise TeardownStopped("upstream hook requires true confirmation")
                if not self.retain_crds:
                    self.crds = [item for item in self.crds if item["spec"]["group"] != "longhorn.io"]
                self.state = None
        return {}


class LonghornTeardownTests(unittest.TestCase):
    def test_confirmation_precedes_connections_and_failures_stop_later_plays(self):
        plays = yaml.safe_load((LAYER / "teardown.yml").read_text())
        self.assertEqual(plays[0]["import_playbook"], "../99-utils/confirm-destroy.yml")
        self.assertEqual(plays[1]["import_playbook"], "../99-utils/kube-connect.yml")
        self.assertTrue(plays[2]["any_errors_fatal"])
        self.assertTrue(plays[2]["tasks"][0]["no_log"])
        for task in plays[2]["tasks"]:
            self.assertNotIn("ignore_errors", task)
            self.assertNotIn("failed_when", task)
        self.assertNotIn("deleting-confirmation-flag", (LAYER / "standup.yml").read_text())

    def test_active_release_sets_flag_before_uninstall_and_preserves_reverse_order(self):
        for state in ("deployed", "failed"):
            for flag in ("false", "true"):
                with self.subTest(state=state, flag=flag):
                    fixture = TeardownFixture(state=state, flag=flag)
                    self.assertEqual(fixture.run(), (["patch"] if flag == "false" else []) +
                                     ["post", "main", "pre", "namespace"])
                    self.assertEqual(fixture.crds, [])

    def test_absent_resources_and_retained_history_need_no_setting_creation(self):
        for state in (None, "uninstalled"):
            with self.subTest(state=state):
                fixture = TeardownFixture(state=state, flag=None, crds=[OTHER_CRD])
                self.assertEqual(fixture.run(), ["post", "main", "pre", "namespace"])
                self.assertIsNone(fixture.flag)
                self.assertEqual(fixture.crds, [OTHER_CRD])

    def test_incomplete_or_busy_install_stops_before_any_mutation(self):
        fixtures = [
            TeardownFixture(state=state) for state in
            ("uninstalling", "pending-install", "pending-upgrade", "pending-rollback", "superseded", "unknown")
        ] + [
            TeardownFixture(state=state, flag=None, crds=crds)
            for state in ("deployed", "failed", None, "uninstalled")
            for crds in ([LONGHORN_CRD], [])
            if state in ("deployed", "failed") or crds
        ]
        for fixture in fixtures:
            with self.subTest(state=fixture.state, crds=fixture.crds):
                with self.assertRaises(TeardownStopped):
                    fixture.run()
                self.assertEqual(fixture.events, [])

    def test_missing_crd_api_stops_before_mutation_or_namespace_cleanup(self):
        class MissingAPI(TeardownFixture):
            def call(self, module, args):
                result = super().call(module, args)
                if module == "kubernetes.core.k8s_info" and args["kind"] == "CustomResourceDefinition":
                    if not self.events or self.state is None:
                        return {"api_found": False, "resources": []}
                return result

        fixture = MissingAPI(state=None, flag=None, crds=[])
        with self.assertRaises(TeardownStopped):
            fixture.run()
        self.assertEqual(fixture.events, [])

        class MissingAfterUninstall(MissingAPI):
            def call(self, module, args):
                if module == "kubernetes.core.k8s_info" and args["kind"] == "CustomResourceDefinition" and not self.events:
                    return {"api_found": True, "resources": copy.deepcopy(self.crds)}
                return super().call(module, args)

        fixture = MissingAfterUninstall()
        with self.assertRaises(TeardownStopped):
            fixture.run()
        self.assertEqual(fixture.events, ["patch", "post", "main"])

    def test_module_failure_never_reaches_later_destructive_steps(self):
        for failure, events in (
            ("patch", ["patch"]),
            ("post", ["patch", "post"]),
            ("main", ["patch", "post", "main"]),
        ):
            with self.subTest(failure=failure):
                fixture = TeardownFixture(fail=failure)
                with self.assertRaises(TeardownStopped):
                    fixture.run()
                self.assertEqual(fixture.events, events)

    def test_remaining_crds_block_pre_and_namespace_cleanup(self):
        fixture = TeardownFixture(retain_crds=True)
        with self.assertRaises(TeardownStopped):
            fixture.run()
        self.assertEqual(fixture.events, ["patch", "post", "main"])

    def test_check_mode_predicts_without_requiring_deletion_to_have_happened(self):
        fixture = TeardownFixture(check=True)
        self.assertEqual(fixture.run(), ["patch", "post", "main", "pre", "namespace"])
        self.assertEqual(fixture.flag, "false")
        self.assertEqual(fixture.crds, [LONGHORN_CRD])


if __name__ == "__main__":
    unittest.main()
