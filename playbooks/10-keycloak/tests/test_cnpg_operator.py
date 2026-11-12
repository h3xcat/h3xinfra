"""Run CNPG ownership guards against isolated local module doubles, never Kubernetes."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


LAYER = Path(__file__).resolve().parents[1]
TASKS = LAYER / "tasks/cnpg-operator.yml"
ANSIBLE = shutil.which("ansible-playbook")
OWNER = "h3xinfra.io/cnpg-operator-owner"
HANDOFF = "yes-terraform-forgot-cnpg"
PIN = "0.29.1"  # Supplied by the synthetic play; no deployment inventory is loaded.
MODULES = {"k8s_info", "helm_info", "helm_repository", "helm_template", "k8s", "helm"}

# Every Kubernetes/Helm module resolves inside a temporary collection. This
# module only reads the fixture and records calls; it has no client or CLI.
MODULE = r'''
import json
import os
from pathlib import Path
from ansible.module_utils.basic import AnsibleModule

module = AnsibleModule(argument_spec={key: {"type": "raw"} for key in ARGUMENTS})
args = module.params
fixture_path = Path(os.environ["CNPG_TEST_FIXTURE"])
fixture = json.loads(fixture_path.read_text())
with open(os.environ["CNPG_TEST_CALLS"], "a") as stream:
    stream.write(json.dumps({"module": NAME, "args": args}) + "\n")
if fixture.get("fail_module") == NAME:
    module.fail_json(msg="synthetic " + NAME + " failure")

if NAME == "k8s_info":
    if any(all(args.get(key) == value for key, value in query.items())
           for query in fixture.get("missing_apis", [])):
        module.exit_json(changed=False, api_found=False, resources=[])
    if args["kind"] == "Namespace":
        resources = fixture["namespaces"]
    elif args["kind"] == "Deployment" and not args.get("namespace"):
        resources = fixture["controllers"]
    else:
        resources = [obj for obj in fixture["objects"] if obj["kind"] == args["kind"]
                     and obj["apiVersion"] == args["api_version"]
                     and (not args.get("name") or obj["metadata"]["name"] == args["name"])
                     and (not args.get("namespace") or
                          obj["metadata"].get("namespace") == args["namespace"])]
    module.exit_json(changed=False, api_found=True, resources=resources)
elif NAME == "helm_info":
    if fixture["release"] is None:
        module.exit_json(changed=False)
    module.exit_json(changed=False, status=fixture["release"])
elif NAME == "helm_template":
    module.exit_json(changed=False, stdout=fixture["template_stdout"])
elif NAME == "k8s":
    definition = args["definition"]
    if definition["kind"] == "Namespace":
        current = fixture["namespaces"][0] if fixture["namespaces"] else {
            "apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "cnpg-system"}}
        for field in ("labels", "annotations"):
            current["metadata"].setdefault(field, {}).update(definition["metadata"].get(field, {}))
        fixture["namespaces"] = [current]
        fixture_path.write_text(json.dumps(fixture))
    module.exit_json(changed=True, result=definition)
elif NAME == "helm":
    fixture["objects"] = fixture["installed_objects"]
    fixture_path.write_text(json.dumps(fixture))
    module.exit_json(changed=True)
else:
    module.exit_json(changed=True)
'''


def task_modules(tasks):
    """Permit only the modules and local file lookup used by this fixture."""
    expressions = json.dumps(tasks).replace(
        "lookup('ansible.builtin.file', playbook_dir ~ '/cnpg-values.yaml')", "")
    if re.search(r"\b(?:lookup|query|q)\s*\(", expressions):
        raise AssertionError("Unexpected lookup in CNPG test tasks")
    arguments = {name: set() for name in MODULES}
    for task in tasks:
        if set(task) & {"delegate_to", "connection", "become", "vars", "environment", "module_defaults"}:
            raise AssertionError("Unexpected execution settings in CNPG test task")
        for block in ("block", "rescue", "always"):
            if block in task:
                for name, keys in task_modules(task[block]).items():
                    arguments[name].update(keys)
        actions = [key for key in task if key.startswith(("ansible.", "kubernetes."))]
        if not actions and "block" in task:
            continue
        if len(actions) != 1:
            raise AssertionError(f"Unexpected task action: {task.get('name')}")
        action, = actions
        if action in {"ansible.builtin.assert", "ansible.builtin.set_fact"}:
            if action == "ansible.builtin.set_fact" and any(
                    not key.startswith("cnpg_") for key in task[action]):
                raise AssertionError("Unexpected fact name in CNPG test task")
            continue
        if action not in {"kubernetes.core." + name for name in MODULES}:
            raise AssertionError(f"Unstubbed action: {action}")
        arguments[action.rsplit(".", 1)[1]].update(task[action])
    return arguments


def fixture(owned=False, existing=False, status="deployed"):
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": "cloudnative-pg", "namespace": "cnpg-system", "labels": {
            "app.kubernetes.io/name": "cloudnative-pg", "app.kubernetes.io/instance": "cloudnative-pg"}},
        "spec": {"replicas": 2, "template": {"spec": {"containers": [
            {"name": "manager", "image": "example.invalid/cnpg:synthetic"}]}}},
    }
    crd = {
        "apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
        "metadata": {"name": "clusters.postgresql.cnpg.io"},
        "spec": {"group": "postgresql.cnpg.io", "scope": "Namespaced",
                 "names": {"kind": "Cluster", "plural": "clusters", "singular": "cluster"}, "versions": [
            {"name": "v1", "served": True, "storage": True,
             "schema": {"openAPIV3Schema": {"type": "object"}}}]},
    }
    manifest = [deployment, crd]
    live = deepcopy(manifest)
    for obj in live:
        obj["metadata"].setdefault("labels", {})["app.kubernetes.io/managed-by"] = "Helm"
        obj["metadata"]["annotations"] = {
            "meta.helm.sh/release-name": "cloudnative-pg",
            "meta.helm.sh/release-namespace": "cnpg-system",
        }
    live[1]["status"] = {"conditions": [{"type": "Established", "status": "True"}]}
    live[0]["metadata"]["generation"] = 1
    live[0]["status"] = {"observedGeneration": 1, "availableReplicas": 2}
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": "cnpg-system", "annotations": {OWNER: "10-keycloak"} if owned else {},
    }}
    release = {
        "name": "cloudnative-pg", "namespace": "cnpg-system",
        "chart": "cloudnative-pg-" + PIN, "status": status,
        "release_values": yaml.safe_load((LAYER / "cnpg-values.yaml").read_text()),
        "manifest": manifest,
    }
    return {
        "namespaces": [namespace] if owned or existing else [],
        "controllers": [live[0]] if existing else [],
        "objects": live if existing else [],
        "installed_objects": deepcopy(live),
        "release": release if existing else None,
        "template_stdout": yaml.safe_dump_all(manifest),
    }


@unittest.skipUnless(ANSIBLE, "ansible-playbook required for isolated CNPG behavior tests")
class CnpgOwnershipTests(unittest.TestCase):
    def run_tasks(self, data, handoff=None, fast_wait=False):
        source = TASKS.read_text()
        tasks = yaml.safe_load(source)
        arguments = task_modules(tasks)
        with tempfile.TemporaryDirectory(prefix="cnpg-ownership-test-") as directory:
            root = Path(directory)
            collection = root / "collections/ansible_collections/kubernetes/core/plugins/modules"
            collection.mkdir(parents=True)
            for name in MODULES:
                (collection / (name + ".py")).write_text(
                    f"#!/usr/bin/python\nNAME = {name!r}\nARGUMENTS = {sorted(arguments[name])!r}\n" + MODULE)
            state = root / "fixture.json"
            state.write_text(json.dumps(data))
            calls = root / "calls.jsonl"
            config = root / "ansible.cfg"
            config.write_text("[defaults]\nretry_files_enabled = False\n")
            shutil.copyfile(LAYER / "cnpg-values.yaml", root / "cnpg-values.yaml")
            if fast_wait:
                # Keep the real readiness expressions; only shorten retry timing.
                for task in tasks:
                    if "until" in task:
                        task.update(retries=1, delay=0)
                source = yaml.safe_dump(tasks)
            snapshot = root / "cnpg-operator.yml"
            snapshot.write_text(source)
            variables = {"chart_versions": {"cloudnative_pg": PIN}}
            if handoff is not None:
                variables["cnpg_operator_handoff"] = handoff
            playbook = root / "test.yml"
            playbook.write_text(yaml.safe_dump([{
                "hosts": "localhost", "connection": "local", "gather_facts": False,
                "vars": variables,
                "tasks": [{"ansible.builtin.import_tasks": str(snapshot)},
                          {"ansible.builtin.copy": {"dest": str(root / "continued"), "content": "ready"}}],
            }]))
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("ANSIBLE_", "K8S_AUTH_", "HELM_"))}
            env.update(ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR="1",
                       ANSIBLE_COLLECTIONS_PATH=str(root / "collections"),
                       ANSIBLE_COLLECTIONS_SCAN_SYS_PATH="false",
                       ANSIBLE_STDOUT_CALLBACK="default", KUBECONFIG="/dev/null",
                       CNPG_TEST_FIXTURE=str(state), CNPG_TEST_CALLS=str(calls))
            result = subprocess.run([
                ANSIBLE, "-i", "localhost,", "-e", "ansible_python_interpreter=" + sys.executable,
                str(playbook),
            ], cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=90)
            recorded = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            self.assertEqual((root / "continued").exists(), result.returncode == 0)
            return result, recorded, json.loads(state.read_text())

    def assert_rejected(self, data, message, handoff=None):
        result, calls, state = self.run_tasks(data, handoff)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stdout + result.stderr)
        self.assertFalse([call for call in calls if call["module"] in {"k8s", "helm"}], calls)
        self.assertEqual(state, data, "preflight changed the namespace marker")
        return calls

    def assert_installed(self, data, handoff=None):
        result, calls, state = self.run_tasks(data, handoff)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        writes = [call for call in calls if call["module"] in {"k8s", "helm"}]
        self.assertEqual([call["module"] for call in writes], ["k8s", "helm"])
        namespace = writes[0]["args"]["definition"]["metadata"]
        self.assertEqual(namespace["name"], "cnpg-system")
        self.assertEqual(namespace["annotations"], {OWNER: "10-keycloak"})
        self.assertEqual(namespace["labels"], {
            "pod-security.kubernetes.io/" + field: "restricted" for field in ("enforce", "audit", "warn")})
        helm = writes[1]["args"]
        self.assertEqual((helm["name"], helm["release_namespace"], helm["chart_version"]),
                         ("cloudnative-pg", "cnpg-system", PIN))
        for key, expected in {"reset_values": False, "reuse_values": True, "atomic": False,
                              "cleanup_on_fail": False, "create_namespace": False, "wait": True}.items():
            self.assertIs(helm[key], expected, key)
        self.assertEqual(helm["state"], "present")
        self.assertNotIn("take_ownership", helm)
        self.assertNotIn("force", helm)
        self.assertEqual(helm["chart_ref"], "cnpg/cloudnative-pg")
        self.assertEqual([Path(path).name for path in helm["values_files"]], ["cnpg-values.yaml"])
        helm_index = next(i for i, call in enumerate(calls) if call["module"] == "helm")
        self.assertEqual([(call["module"], call["args"].get("kind"), call["args"].get("name"))
                          for call in calls[helm_index + 1:]], [
            ("k8s_info", "CustomResourceDefinition", "clusters.postgresql.cnpg.io"),
            ("k8s_info", "Deployment", "cloudnative-pg"),
        ])
        read = next(call for call in calls if call["module"] == "helm_info")
        self.assertEqual(read["args"], {"name": "cloudnative-pg", "release_namespace": "cnpg-system"})
        namespace_read = next(call for call in calls
                              if call["module"] == "k8s_info" and call["args"]["kind"] == "Namespace")
        self.assertEqual((namespace_read["args"]["api_version"], namespace_read["args"]["name"]),
                         ("v1", "cnpg-system"))
        controller_read = next(call for call in calls if call["module"] == "k8s_info"
                               and call["args"]["kind"] == "Deployment" and call["args"]["label_selectors"])
        self.assertIsNone(controller_read["args"]["namespace"])
        self.assertEqual(controller_read["args"]["label_selectors"], ["app.kubernetes.io/name=cloudnative-pg"])
        for call in calls:
            if call["module"] == "helm_template":
                self.assertEqual((call["args"]["name"], call["args"]["release_namespace"],
                                  call["args"]["chart_ref"], call["args"]["chart_version"]),
                                 ("cloudnative-pg", "cnpg-system", "cnpg/cloudnative-pg", PIN))
                self.assertIs(call["args"]["include_crds"], True)
                self.assertEqual(call["args"]["values_files"], helm["values_files"])
        return calls, state

    def test_fresh_install_without_namespace(self):
        self.assert_installed(fixture())

    def test_missing_initial_api_discovery_fails_before_writes(self):
        for query in (
            {"api_version": "v1", "kind": "Namespace", "name": "cnpg-system"},
            {"api_version": "apps/v1", "kind": "Deployment", "namespace": None},
            {"api_version": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
             "name": "clusters.postgresql.cnpg.io"},
        ):
            with self.subTest(query=query):
                data = fixture()
                data["missing_apis"] = [query]
                calls = self.assert_rejected(data, "Required Kubernetes API discovery failed", HANDOFF)
                self.assertTrue(any(call["module"] == "k8s_info" and
                                    all(call["args"].get(key) == value for key, value in query.items())
                                    for call in calls))
                self.assertFalse([call for call in calls if call["module"] in {"helm_repository", "helm_template"}])

    def test_missing_namespace_occupancy_api_fails_before_writes(self):
        data = fixture(owned=True)
        query = {"api_version": "cilium.io/v2", "kind": "CiliumNetworkPolicy", "namespace": "cnpg-system"}
        data["missing_apis"] = [query]
        calls = self.assert_rejected(data, "Require an empty namespace before a fresh installation", HANDOFF)
        self.assertTrue(any(call["module"] == "k8s_info" and
                            all(call["args"].get(key) == value for key, value in query.items()) for call in calls))
        self.assertFalse([call for call in calls if call["module"] in {"helm_repository", "helm_template"}])

    def test_missing_cluster_scope_api_fails_before_writes(self):
        for version, kind in (("rbac.authorization.k8s.io/v1", "ClusterRole"),
                              ("admissionregistration.k8s.io/v1", "ValidatingWebhookConfiguration")):
            with self.subTest(kind=kind):
                data = fixture()
                obj = {"apiVersion": version, "kind": kind, "metadata": {"name": "cnpg-test"}}
                manifest = list(yaml.safe_load_all(data["template_stdout"])) + [obj]
                data["template_stdout"] = yaml.safe_dump_all(manifest)
                query = {"api_version": version, "kind": kind, "name": "cnpg-test"}
                data["missing_apis"] = [query]
                calls = self.assert_rejected(data, "Refuse orphaned cluster-scoped operator objects", HANDOFF)
                self.assertTrue(any(call["module"] == "k8s_info" and
                                    all(call["args"].get(key) == value for key, value in query.items())
                                    for call in calls))

    def test_empty_precreated_namespace_allows_only_kubernetes_defaults(self):
        data = fixture(owned=True)
        data["namespaces"][0]["metadata"]["annotations"] = {}
        data["objects"] += [{"apiVersion": "v1", "kind": kind, "metadata": {
            "name": name, "namespace": "cnpg-system"}}
            for kind, name in [("ServiceAccount", "default"), ("ConfigMap", "kube-root-ca.crt")]]
        self.assert_installed(data)

    def test_public_owned_release_needs_no_handoff(self):
        self.assert_installed(fixture(owned=True, existing=True))

    def test_owned_deployed_release_can_receive_a_later_chart_pin(self):
        data = fixture(owned=True, existing=True)
        data["release"]["chart"] = "cloudnative-pg-0.28.0"
        self.assert_installed(data)

    def test_owned_release_can_apply_reviewed_values_after_deployment_or_failure(self):
        for status in ("deployed", "failed"):
            with self.subTest(status=status):
                data = fixture(owned=True, existing=True, status=status)
                data["release"]["release_values"] = {"previousReviewedSetting": True}
                # assert_installed checks the reviewed values file and retained-values
                # Helm flags, including reuse_values=true and reset_values=false.
                self.assert_installed(data)

    def test_unmarked_release_requires_exact_handoff(self):
        for handoff in (None, "yes"):
            with self.subTest(handoff=handoff):
                self.assert_rejected(fixture(existing=True), "Terraform must first forget", handoff)

    def test_matching_unmarked_release_is_adopted_with_acknowledgement(self):
        calls, _ = self.assert_installed(fixture(existing=True), HANDOFF)
        self.assertLess(next(i for i, call in enumerate(calls) if call["module"] == "helm_template"),
                        next(i for i, call in enumerate(calls) if call["module"] == "k8s"))

    def test_failed_helm_preserves_marker_and_allows_owned_retry(self):
        data = fixture()
        data["fail_module"] = "helm"
        result, calls, state = self.run_tasks(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic helm failure", result.stdout + result.stderr)
        self.assertEqual([call["module"] for call in calls if call["module"] in {"k8s", "helm"}],
                         ["k8s", "helm"])
        self.assertEqual(state["namespaces"][0]["metadata"]["annotations"][OWNER], "10-keycloak")
        state.pop("fail_module")
        self.assert_installed(state)
        self.assert_installed(fixture(owned=True, existing=True, status="failed"))

    def test_owned_empty_namespace_can_retry_a_failed_first_install(self):
        self.assert_installed(fixture(owned=True))

    def test_namespace_ownership_and_security_mismatches_cannot_be_acknowledged(self):
        cases = [
            (("name",), "other-namespace"),
            (("deletionTimestamp",), "2026-01-01T00:00:00Z"),
            (("annotations", OWNER), "other-owner"),
            (("annotations", "meta.helm.sh/release-name"), "other-release"),
            (("annotations", "meta.helm.sh/release-namespace"), "other-namespace"),
        ]
        cases.extend([(("labels", "pod-security.kubernetes.io/" + field), "privileged")
                      for field in ("enforce", "audit", "warn")])
        for path, value in cases:
            with self.subTest(path=path):
                data = fixture(existing=True)
                current = data["namespaces"][0]["metadata"]
                for key in path[:-1]:
                    current = current.setdefault(key, {})
                current[path[-1]] = value
                self.assert_rejected(data, "unexpected ownership, deletion or PSA", HANDOFF)

    def test_existing_release_identity_values_and_states_fail_before_writes(self):
        for field, value in (("name", "other-release"), ("namespace", "other-namespace"),
                             ("chart", "unrelated-" + PIN),
                             ("status", "pending-install"), ("status", "pending-upgrade"),
                             ("status", "pending-rollback"), ("status", "uninstalling")):
            with self.subTest(field=field, value=value):
                data = fixture(owned=True, existing=True)
                data["release"][field] = value
                self.assert_rejected(data, "unexpected identity, state or values", HANDOFF)
        data = fixture(existing=True)
        data["release"]["release_values"] = {"unexpected": True}
        self.assert_rejected(data, "unexpected identity, state or values", HANDOFF)
        data = fixture(existing=True, status="failed")
        self.assert_rejected(data, "unexpected identity, state or values", HANDOFF)
        data = fixture(owned=True, existing=True, status="failed")
        data["release"]["chart"] = "cloudnative-pg-0.28.0"
        self.assert_rejected(data, "unexpected identity, state or values", HANDOFF)
        data = fixture(existing=True)
        data["namespaces"] = []
        self.assert_rejected(data, "unexpected identity, state or values", HANDOFF)
        data = fixture(existing=True)
        data["controllers"] = []
        self.assert_rejected(data, "unexpected identity, state or values", HANDOFF)

    def test_foreign_or_orphaned_controller_is_never_adopted(self):
        for field in ("name", "namespace"):
            with self.subTest(field=field):
                data = fixture(existing=True)
                data["controllers"][0]["metadata"][field] = "other"
                self.assert_rejected(data, "unexpected or orphaned CNPG controller", HANDOFF)
        data = fixture(existing=True)
        data["release"] = None
        data["objects"] = [obj for obj in data["objects"] if obj["kind"] != "CustomResourceDefinition"]
        self.assert_rejected(data, "unexpected or orphaned CNPG controller", HANDOFF)

    def test_orphaned_crds_cannot_be_adopted_without_helm_history(self):
        for owned in (False, True):
            with self.subTest(owned=owned):
                data = fixture(owned=owned)
                data["objects"] = [deepcopy(data["installed_objects"][1])]
                self.assert_rejected(data, "CNPG schemas already exist without the expected Helm release", HANDOFF)

    def test_orphaned_cluster_role_cannot_be_adopted_without_helm_history(self):
        data = fixture()
        role = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
                "metadata": {"name": "cloudnative-pg"}, "rules": []}
        data["objects"] = [role]
        manifest = list(yaml.safe_load_all(data["template_stdout"])) + [role]
        data["template_stdout"] = yaml.safe_dump_all(manifest)
        self.assert_rejected(data, "Refuse orphaned cluster-scoped operator objects", HANDOFF)

    def test_controller_helm_ownership_and_image_drift_fail_before_writes(self):
        for field, value in (("meta.helm.sh/release-name", "other"),
                             ("meta.helm.sh/release-namespace", "other")):
            with self.subTest(field=field):
                data = fixture(owned=True, existing=True)
                data["controllers"][0]["metadata"]["annotations"][field] = value
                self.assert_rejected(data, "unexpected Helm ownership or image drift")
        data = fixture(owned=True, existing=True)
        data["controllers"][0]["metadata"]["labels"]["app.kubernetes.io/managed-by"] = "other"
        self.assert_rejected(data, "unexpected Helm ownership or image drift")
        data = fixture(owned=True, existing=True)
        data["controllers"][0]["spec"]["template"]["spec"]["containers"][0]["image"] = "other.invalid/image"
        self.assert_rejected(data, "unexpected Helm ownership or image drift")

    def test_existing_namespace_configuration_is_not_a_fresh_install(self):
        for kind in ("Pod", "Secret", "ServiceAccount", "ConfigMap", "Role", "RoleBinding",
                     "NetworkPolicy", "CiliumNetworkPolicy", "ResourceQuota", "LimitRange",
                     "ReplicaSet", "ReplicationController"):
            with self.subTest(kind=kind):
                data = fixture(owned=True)
                version = {"Role": "rbac.authorization.k8s.io/v1", "RoleBinding": "rbac.authorization.k8s.io/v1",
                           "NetworkPolicy": "networking.k8s.io/v1", "CiliumNetworkPolicy": "cilium.io/v2",
                           "ReplicaSet": "apps/v1"}.get(kind, "v1")
                data["objects"].append({"apiVersion": version, "kind": kind,
                                        "metadata": {"name": "existing", "namespace": "cnpg-system"}})
                calls = self.assert_rejected(data, "Require an empty namespace before a fresh installation", HANDOFF)
                self.assertTrue(any(call["module"] == "k8s_info" and
                                    call["args"].get("kind") == kind and
                                    call["args"].get("api_version") == version and
                                    call["args"].get("namespace") == "cnpg-system" for call in calls))
                self.assertFalse([call for call in calls if call["module"] in {"helm_repository", "helm_template"}])

    def test_adoption_requires_exact_chart_and_complete_stored_manifest(self):
        data = fixture(existing=True)
        data["release"]["chart"] = "cloudnative-pg-0.28.0"
        self.assert_rejected(data, "differ from the pinned source", HANDOFF)
        for change in ("field", "missing", "extra"):
            with self.subTest(change=change):
                data = fixture(existing=True)
                if change == "field":
                    data["release"]["manifest"][1]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["description"] = "changed"
                elif change == "missing":
                    data["release"]["manifest"].pop()
                else:
                    data["release"]["manifest"].append({"apiVersion": "v1", "kind": "ConfigMap",
                                                       "metadata": {"name": "unexpected"}})
                self.assert_rejected(data, "differ from the pinned source", HANDOFF)

    def test_adoption_rejects_live_crd_ownership_and_schema_drift(self):
        paths = [
            ("metadata", "annotations", "meta.helm.sh/release-name"),
            ("metadata", "annotations", "meta.helm.sh/release-namespace"),
            ("metadata", "labels", "app.kubernetes.io/managed-by"),
            ("spec", "group"), ("spec", "scope"), ("spec", "names", "kind"),
            ("spec", "versions", 0, "schema", "openAPIV3Schema", "type"),
        ]
        for path in paths:
            with self.subTest(path=path):
                data = fixture(existing=True)
                current = data["objects"][1]
                for key in path[:-1]:
                    current = current[key]
                current[path[-1]] = "other"
                self.assert_rejected(data, "live CNPG CRD ownership or schema differs", HANDOFF)
        for field, value in (("conversion", {"strategy": "Webhook"}), ("preserveUnknownFields", True)):
            with self.subTest(field=field):
                data = fixture(existing=True)
                data["objects"][1]["spec"][field] = value
                self.assert_rejected(data, "live CNPG CRD ownership or schema differs", HANDOFF)
        for field in ("served", "storage"):
            with self.subTest(field=field):
                data = fixture(existing=True)
                data["objects"][1]["spec"]["versions"][0][field] = False
                self.assert_rejected(data, "live CNPG CRD ownership or schema differs", HANDOFF)
        for field, value in (("additionalPrinterColumns", [{"name": "Extra", "type": "string", "jsonPath": ".spec.extra"}]),
                             ("selectableFields", [{"jsonPath": ".spec.extra"}]),
                             ("deprecated", True), ("deprecationWarning", "test warning")):
            with self.subTest(field=field):
                data = fixture(existing=True)
                data["objects"][1]["spec"]["versions"][0][field] = value
                self.assert_rejected(data, "live CNPG CRD ownership or schema differs", HANDOFF)

    def test_read_and_render_failures_stop_before_cluster_writes(self):
        for module in ("k8s_info", "helm_info", "helm_template"):
            with self.subTest(module=module):
                data = fixture(existing=True)
                data["fail_module"] = module
                message = ("Read all states of the shared CloudNativePG Helm release" if module == "helm_info"
                           else "synthetic " + module + " failure")
                calls = self.assert_rejected(data, message, HANDOFF)
                self.assertEqual(calls[-1]["module"], module, "the intended failing stub was not reached")

    def test_failed_namespace_write_never_calls_helm(self):
        data = fixture()
        data["fail_module"] = "k8s"
        result, calls, state = self.run_tasks(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("synthetic k8s failure", result.stdout + result.stderr)
        self.assertFalse([call for call in calls if call["module"] == "helm"])
        self.assertEqual(state, data)

    def test_readiness_failure_stops_before_following_keycloak_tasks(self):
        for failure in ("crd-not-established", "crd-v1-not-served", "old-generation", "no-available-controller"):
            with self.subTest(failure=failure):
                data = fixture()
                deployment, crd = data["installed_objects"]
                if failure == "crd-not-established":
                    crd["status"]["conditions"] = []
                elif failure == "crd-v1-not-served":
                    crd["spec"]["versions"][0]["served"] = False
                elif failure == "old-generation":
                    deployment["status"]["observedGeneration"] = 0
                else:
                    deployment["status"]["availableReplicas"] = 0
                result, calls, _ = self.run_tasks(data, fast_wait=True)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual([call["module"] for call in calls if call["module"] in {"k8s", "helm"}],
                                 ["k8s", "helm"])
                expected = "CustomResourceDefinition" if failure.startswith("crd-") else "Deployment"
                self.assertEqual(calls[-1]["args"]["kind"], expected)


class CnpgOrderingTests(unittest.TestCase):
    def test_operator_is_ready_before_the_first_keycloak_write(self):
        plays = yaml.safe_load((LAYER / "standup.yml").read_text())
        deploy = next(play for play in plays if play.get("name") == "Deploy Keycloak (Operator + CNPG)")
        self.assertEqual(deploy["tasks"][0]["ansible.builtin.import_tasks"], "tasks/cnpg-operator.yml")

    def test_operator_tag_selects_only_connection_and_operator_tasks(self):
        selected = []
        for play in yaml.safe_load((LAYER / "standup.yml").read_text()):
            tags = play.get("tags", [])
            tags = [tags] if isinstance(tags, str) else tags
            if set(tags) & {"cnpg-operator", "always"}:
                self.assertIn("import_playbook", play)
                selected.append(play["import_playbook"])
            for task in play.get("tasks", []):
                tags = task.get("tags", [])
                tags = [tags] if isinstance(tags, str) else tags
                if set(tags) & {"cnpg-operator", "always"}:
                    self.assertIn("ansible.builtin.import_tasks", task)
                    selected.append(task["ansible.builtin.import_tasks"])
        self.assertEqual(selected, ["../99-utils/kube-connect.yml", "tasks/cnpg-operator.yml"])


if __name__ == "__main__":
    unittest.main()
