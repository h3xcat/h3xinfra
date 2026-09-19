"""Verify supported Longhorn resource controls and unused-V2 safeguards."""

import copy
import json
import os
from pathlib import Path
import subprocess
import unittest

from jinja2 import ChainableUndefined, Environment
import yaml


PLAYBOOK = Path(__file__).resolve().parents[1] / "standup.yml"
CHART = os.environ.get("LONGHORN_CHART_PATH")
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


def v2_enabled(context=None):
    env = Environment(undefined=ChainableUndefined)
    env.filters["bool"] = bool
    expression = helm_values()["defaultSettings"]["v2DataEngine"]
    return yaml.safe_load(env.from_string(expression).render(context or {}))


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

    def test_cpu_reservations_keep_live_values_with_correct_case(self):
        settings = helm_values()["defaultSettings"]
        self.assertNotIn("guaranteedInstanceManagerCpu", settings)
        self.assertEqual(settings["guaranteedInstanceManagerCPU"], {"v1": "12", "v2": "31"})

    def test_v2_defaults_off_and_prerequisites_require_explicit_opt_in(self):
        self.assertIs(v2_enabled(), False)
        self.assertIs(v2_enabled({"longhorn_v2_data_engine": True}), True)
        self.assertIs(v2_enabled({"longhorn_v2_data_engine": "false"}), False)
        self.assertIs(v2_enabled({"longhorn_v2_data_engine": "true"}), True)
        preparation = next(play for play in plays() if play.get("name", "").startswith("Prepare amd64"))
        self.assertIn("longhorn_v2_data_engine | default(false) | bool", preparation["tasks"][0]["when"])

    def test_v2_helm_default_works_with_existing_shared_renderer(self):
        # Public-core CI reads the shared renderer before the private PR merges.
        self.assertEqual(
            helm_values()["defaultSettings"]["v2DataEngine"],
            "{{ longhorn_v2_data_engine | default(false) }}",
        )

    def test_v2_disable_guard_precedes_any_helm_update(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        names = [task["name"] for task in tasks]
        guard = tasks[names.index("Verify the V2 data engine is unused before disabling it")]
        self.assertLess(names.index(guard["name"]), names.index("Deploy Longhorn via Helm"))
        self.assertIn("not (longhorn_v2_data_engine | default(false) | bool)", guard["when"])
        assertions = [task["ansible.builtin.assert"]["that"] for task in guard["block"] if "ansible.builtin.assert" in task]
        self.assertEqual(assertions, [
            ["longhorn_existing_volumes.resources | selectattr('spec.dataEngine', 'equalto', 'v2') | list | length == 0"],
            ["item.status.instanceEngines | default({}) | length == 0", "item.status.instanceReplicas | default({}) | length == 0"],
        ])

    def test_existing_storage_and_recovery_policy_is_preserved(self):
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
            "nodeDownPodDeletionPolicy": "delete-both-statefulset-and-deployment-pod",
            "nodeDrainPolicy": "block-for-eviction-if-contains-last-replica",
            "orphanResourceAutoDeletion": "instance",
            "orphanResourceAutoDeletionGracePeriod": "300",
        }
        self.assertEqual({key: settings[key] for key in expected}, expected)
        for key in ("engineReplicaTimeout", "replicaReplenishmentWaitInterval", "concurrentReplicaRebuildPerNodeLimit"):
            self.assertNotIn(key, settings)

    def test_disable_assertions_reject_volumes_and_active_processes(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        guard = next(task for task in tasks if task["name"] == "Verify the V2 data engine is unused before disabling it")
        assertions = [task["ansible.builtin.assert"]["that"] for task in guard["block"] if "ansible.builtin.assert" in task]
        env = Environment(undefined=ChainableUndefined)
        volume_check = env.compile_expression(assertions[0][0])
        self.assertTrue(volume_check(longhorn_existing_volumes={"resources": [{"spec": {"dataEngine": "v1"}}]}))
        self.assertFalse(volume_check(longhorn_existing_volumes={"resources": [{"spec": {"dataEngine": "v2"}}]}))
        process_checks = [env.compile_expression(expression) for expression in assertions[1]]
        self.assertTrue(all(check(item={"status": {"instanceEngines": {}, "instanceReplicas": {}}}) for check in process_checks))
        for field in ("instanceEngines", "instanceReplicas"):
            with self.subTest(field=field):
                self.assertFalse(all(check(item={"status": {field: {"active": {}}}}) for check in process_checks))

    def test_disable_postcondition_waits_for_native_setting_and_manager_removal(self):
        tasks = next(play for play in plays() if play.get("name") == "Deploy Longhorn via Helm")["tasks"]
        names = [task["name"] for task in tasks]
        post = tasks[names.index("Verify the V2 data engine has stopped after disabling it")]
        self.assertGreater(names.index(post["name"]), names.index("Deploy Longhorn via Helm"))
        self.assertEqual(post["when"], "not (longhorn_v2_data_engine | default(false) | bool)")
        setting, managers = post["block"]
        self.assertEqual(setting["kubernetes.core.k8s_info"]["name"], "v2-data-engine")
        self.assertEqual(setting["until"], [
            "longhorn_v2_setting.resources | length == 1",
            "longhorn_v2_setting.resources[0].value == 'false'",
        ])
        env = Environment(undefined=ChainableUndefined)
        absent = env.compile_expression(managers["until"])
        self.assertTrue(absent(longhorn_remaining_instance_managers={"resources": []}))
        self.assertTrue(absent(longhorn_remaining_instance_managers={"resources": [{"spec": {"dataEngine": "v1"}}]}))
        self.assertFalse(absent(longhorn_remaining_instance_managers={"resources": [{"spec": {"dataEngine": "v2"}}]}))
        for task in (setting, managers):
            self.assertEqual(task["retries"], 60)
            self.assertEqual(task["delay"], 5)
            self.assertNotIn("ignore_errors", task)
            self.assertNotIn("failed_when", task)


@unittest.skipUnless(CHART, "Set LONGHORN_CHART_PATH to the deployed Longhorn chart")
class LonghornChartRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        values = helm_values()
        values["defaultSettings"]["v2DataEngine"] = v2_enabled()
        values["defaultBackupStore"] = {
            "backupTarget": "cifs://192.0.2.10/test-only",
            "backupTargetCredentialSecret": "test-only-backup-credential",
        }
        result = subprocess.run(
            [os.environ.get("HELM", "helm"), "template", "test-longhorn", CHART, "-n", "longhorn-system", "-f", "-"],
            input=yaml.safe_dump(values), capture_output=True, text=True, check=True, timeout=120,
        )
        cls.documents = [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]

    def test_manager_daemonset_has_budget_and_one_at_a_time_strategy(self):
        manager = next(doc for doc in self.documents if doc["kind"] == "DaemonSet" and doc["metadata"]["name"] == "longhorn-manager")
        self.assertEqual(manager["spec"]["updateStrategy"]["rollingUpdate"]["maxUnavailable"], 1)
        container = next(c for c in manager["spec"]["template"]["spec"]["containers"] if c["name"] == "longhorn-manager")
        self.assertEqual(container["resources"], MANAGER)

    def test_native_settings_serialize_cpu_csi_and_v2_correctly(self):
        config = next(doc for doc in self.documents if doc["kind"] == "ConfigMap" and "default-setting.yaml" in doc.get("data", {}))
        settings = yaml.safe_load(config["data"]["default-setting.yaml"])
        self.assertEqual(json.loads(settings["guaranteed-instance-manager-cpu"]), {"v1": "12", "v2": "31"})
        self.assertIs(settings["v2-data-engine"], False)
        self.assertEqual(
            json.loads(settings["system-managed-csi-components-resource-limits"]),
            json.loads(helm_values()["defaultSettings"]["systemManagedCSIComponentsResourceLimits"]),
        )


if __name__ == "__main__":
    unittest.main()
