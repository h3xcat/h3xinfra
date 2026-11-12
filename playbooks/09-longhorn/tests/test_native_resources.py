"""Verify supported Longhorn resource controls and unused-V2 safeguards."""

import copy
from itertools import product
import json
from pathlib import Path
import unittest

from jinja2 import ChainableUndefined, Environment
import yaml


PLAYBOOK = Path(__file__).resolve().parents[1] / "standup.yml"
SIDECARS = {"csi-attacher", "csi-provisioner", "csi-resizer", "csi-snapshotter"}
MANAGER = {
    "requests": {"cpu": "1", "memory": "3Gi"},
    "limits": {"cpu": "4", "memory": "8Gi"},
}


def plays():
    return yaml.safe_load(PLAYBOOK.read_text())


def helm_values():
    for play in plays():
        for task in play.get("tasks", []):
            if task.get("name") == "Deploy Longhorn via Helm":
                return copy.deepcopy(task["kubernetes.core.helm"]["values"])
    raise AssertionError("Longhorn Helm task missing")


def budget(cpu_request, memory_request, cpu_limit, memory_limit):
    return {
        "requests": {"cpu": cpu_request, "memory": memory_request, "ephemeral-storage": "32Mi"},
        "limits": {"cpu": cpu_limit, "memory": memory_limit, "ephemeral-storage": "256Mi"},
    }


class LonghornNativeResourceTests(unittest.TestCase):
    def test_manager_budget_and_serial_rollout(self):
        manager = helm_values()["longhornManager"]
        self.assertEqual(manager["resources"], MANAGER)
        self.assertEqual(manager["updateStrategy"], {"rollingUpdate": {"maxUnavailable": 1}})

    def test_native_csi_setting_is_json_with_all_supported_components(self):
        value = helm_values()["defaultSettings"]["systemManagedCSIComponentsResourceLimits"]
        self.assertIsInstance(value, str)
        actual = json.loads(value)
        expected = {name: budget("50m", "64Mi", "500m", "256Mi") for name in SIDECARS}
        expected["longhorn-csi-plugin"] = budget("100m", "128Mi", "1", "512Mi")
        for name in ("node-driver-registrar", "longhorn-liveness-probe"):
            expected[name] = budget("25m", "32Mi", "250m", "128Mi")
        self.assertEqual(actual, expected)

    def test_cpu_reservations_keep_current_values(self):
        settings = helm_values()["defaultSettings"]
        self.assertEqual(settings["guaranteedInstanceManagerCPU"], {"v1": "12", "v2": "31"})

    def test_v2_is_disabled_without_an_inventory_switch(self):
        self.assertIs(helm_values()["defaultSettings"]["v2DataEngine"], False)

    def test_v2_disable_guard_precedes_any_helm_update(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        names = [task["name"] for task in tasks]
        guard = tasks[names.index("Verify the V2 data engine is unused before disabling it")]
        self.assertLess(names.index(guard["name"]), names.index("Deploy Longhorn via Helm"))
        self.assertNotIn("ignore_errors", guard)
        self.assertEqual(guard["when"], "longhorn_volume_crd.resources | length > 0")
        for task in guard["block"]:
            self.assertNotIn("ignore_errors", task)
            self.assertNotIn("failed_when", task)

    def test_v2_api_guard_allows_only_clean_installation_or_complete_apis(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        guard = next(task for task in tasks if task["name"] == "Require complete Longhorn APIs before changing the V2 setting")
        expressions = guard["ansible.builtin.assert"]["that"]
        env = Environment(undefined=ChainableUndefined)
        for volume_present, manager_present, volume_api, manager_api in product((False, True), repeat=4):
            with self.subTest(volumes=volume_present, managers=manager_present, volume_api=volume_api, manager_api=manager_api):
                context = {
                    "longhorn_volume_crd": {"api_found": volume_api, "resources": [{}] if volume_present else []},
                    "longhorn_instance_manager_crd": {"api_found": manager_api, "resources": [{}] if manager_present else []},
                }
                allowed = all(env.compile_expression(item)(**context) for item in expressions)
                self.assertEqual(allowed, volume_api and manager_api and volume_present == manager_present)

    def test_storage_and_attended_recovery_policy(self):
        settings = helm_values()["defaultSettings"]
        expected = {
            "defaultDataPath": "/var/lib/longhorn/",
            "storageMinimalAvailablePercentage": 10,
            "storageOverProvisioningPercentage": 200,
            "storageReservedPercentageForDefaultDisk": 5,
            "defaultReplicaCount": 3,
            "defaultDataLocality": "best-effort",
            "replicaAutoBalance": "least-effort",
            "replicaSoftAntiAffinity": "false",
            "allowVolumeCreationWithDegradedAvailability": "false",
            "rwxVolumeFastFailover": "true",
            "nodeDownPodDeletionPolicy": "do-nothing",
            "concurrentAutomaticEngineUpgradePerNodeLimit": 0,
            "nodeDrainPolicy": "block-for-eviction-if-contains-last-replica",
            "orphanResourceAutoDeletion": "instance",
            "orphanResourceAutoDeletionGracePeriod": "300",
        }
        self.assertEqual({key: settings[key] for key in expected}, expected)

    def test_disable_assertions_reject_volumes_and_active_processes(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        guard = next(task for task in tasks if task["name"] == "Verify the V2 data engine is unused before disabling it")
        assertions = {task["name"]: task["ansible.builtin.assert"]["that"]
                      for task in guard["block"] if "ansible.builtin.assert" in task}
        env = Environment(undefined=ChainableUndefined)
        volume_checks = assertions["Refuse to disable V2 while any V2 volume exists"]
        for api_found, engine, expected in ((True, "v1", True), (True, "v2", False), (False, "v1", False)):
            with self.subTest(api_found=api_found, engine=engine):
                context = {"longhorn_existing_volumes": {"api_found": api_found, "resources": [{"spec": {"dataEngine": engine}}]}}
                self.assertEqual(all(env.compile_expression(item)(**context) for item in volume_checks), expected)
        api_check = env.compile_expression(assertions["Require successful instance manager discovery"])
        for found in (False, True):
            self.assertEqual(api_check(longhorn_existing_instance_managers={"api_found": found}), found)
        process_checks = [env.compile_expression(item) for item in
                          assertions["Refuse to disable V2 while its instance managers contain processes"]]
        self.assertTrue(all(check(item={"status": {}}) for check in process_checks))
        for field in ("instanceEngines", "instanceEngineFrontends", "instanceReplicas", "instanceShards", "instanceShardGroups"):
            with self.subTest(field=field):
                self.assertFalse(all(check(item={"status": {field: {"active": {}}}}) for check in process_checks))

    def test_disable_postcondition_waits_for_native_setting_and_manager_removal(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        names = [task["name"] for task in tasks]
        post = tasks[names.index("Verify the V2 data engine has stopped after disabling it")]
        self.assertGreater(names.index(post["name"]), names.index("Deploy Longhorn via Helm"))
        self.assertEqual(post["when"], "not ansible_check_mode")
        run_postcondition = Environment().compile_expression(post["when"])
        self.assertTrue(run_postcondition(ansible_check_mode=False))
        self.assertFalse(run_postcondition(ansible_check_mode=True))
        setting, managers = post["block"]
        self.assertEqual(setting["kubernetes.core.k8s_info"]["name"], "v2-data-engine")
        self.assertEqual(setting["until"], [
            "longhorn_v2_setting.resources | length == 1",
            "longhorn_v2_setting.resources[0].value == 'false'",
        ])
        env = Environment(undefined=ChainableUndefined)
        absent = env.compile_expression(managers["until"])
        self.assertTrue(absent(longhorn_remaining_instance_managers={"api_found": True, "resources": []}))
        self.assertTrue(absent(longhorn_remaining_instance_managers={"api_found": True, "resources": [{"spec": {"dataEngine": "v1"}}]}))
        self.assertFalse(absent(longhorn_remaining_instance_managers={"api_found": True, "resources": [{"spec": {"dataEngine": "v2"}}]}))
        self.assertFalse(absent(longhorn_remaining_instance_managers={"api_found": False, "resources": []}))
        for task in (setting, managers):
            self.assertEqual(task["retries"], 60)
            self.assertEqual(task["delay"], 5)
            self.assertNotIn("ignore_errors", task)
            self.assertNotIn("failed_when", task)

    def test_auth_method_guard_runs_first_and_accepts_only_supported_methods(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        names = [task["name"] for task in tasks]
        self.assertEqual(names[0], "Validate Longhorn auth method")
        self.assertLess(names.index("Validate Longhorn auth method"), names.index("Deploy Longhorn via Helm"))
        self.assertLess(names.index("Validate Longhorn auth method"),
                        names.index("Deploy Longhorn configuration via Helm"))
        guard = tasks[0]
        self.assertNotIn("when", guard)
        self.assertNotIn("ignore_errors", guard)
        that = guard["ansible.builtin.assert"]["that"]
        self.assertEqual(that, ["(longhorn.auth.method | default('none')) in ['none', 'oauth2_proxy']"])
        check = Environment(undefined=ChainableUndefined).compile_expression(that[0])
        for context in (
            {"longhorn": {"auth": {"method": "oauth2_proxy"}}},
            {"longhorn": {"auth": {"method": "none"}}},
            {"longhorn": {"auth": {}}},
            {"longhorn": {}},
        ):
            with self.subTest(context=context):
                self.assertTrue(check(**context))
        for method in ("basic", "oauth2-proxy", "OAUTH2_PROXY", ""):
            with self.subTest(method=method):
                self.assertFalse(check(longhorn={"auth": {"method": method}}))


if __name__ == "__main__":
    unittest.main()
