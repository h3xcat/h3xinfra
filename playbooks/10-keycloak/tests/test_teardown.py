"""Evaluate the teardown's actual safety predicates using synthetic API replies.

No Ansible playbook, cluster command, Terraform operation or secret is executed.
These tests cover refusal and ordering; they do not prove live API behavior.
"""

from copy import deepcopy
from pathlib import Path
import unittest

from ansible.errors import AnsibleError
from ansible.template import Templar, trust_as_template
import yaml


LAYER = Path(__file__).resolve().parents[1]
PLAYS = yaml.safe_load((LAYER / "teardown.yml").read_text())
TASKS = {task["name"]: task for play in PLAYS for task in play.get("tasks", [])}
KEYCLOAK = {"namespace": "identity", "release_name_prefix": "example-keycloak"}
def evaluate(expression, **context):
    templar = Templar(variables={"keycloak": KEYCLOAK, **context})
    if isinstance(expression, str) and expression.startswith("{{"):
        return templar.template(trust_as_template(expression))
    return templar.evaluate_conditional(trust_as_template(expression) if isinstance(expression, str) else expression)


def accepts(name, item):
    predicates = TASKS[name]["ansible.builtin.assert"]["that"]
    if not isinstance(predicates, list):
        predicates = [predicates]
    try:
        return all(evaluate(predicate, item=item) for predicate in predicates)
    except AnsibleError:
        return False


def owned_keycloak():
    return {
        "kind": "Keycloak",
        "metadata": {
            "name": "example-keycloak-main",
            "namespace": "identity",
            "annotations": {
                "meta.helm.sh/release-name": "example-keycloak-main",
                "meta.helm.sh/release-namespace": "identity",
            },
        },
    }


def binding(kind="RoleBinding", namespace="identity", subject_namespace=None):
    metadata = {"name": "keycloakcontroller-role-binding"}
    subject = {"kind": "ServiceAccount", "name": "keycloak-operator"}
    if kind == "RoleBinding":
        metadata["namespace"] = namespace
    else:
        metadata["name"] = "keycloak-operator-clusterrole-binding"
        subject["namespace"] = namespace
    if subject_namespace is not None:
        subject["namespace"] = subject_namespace
    return {"kind": kind, "metadata": metadata, "subjects": [subject],
            "roleRef": {"kind": "ClusterRole", "name": "keycloakcontroller-cluster-role"}}


class TeardownTests(unittest.TestCase):
    def test_confirmation_and_preflight_precede_all_mutations(self):
        self.assertEqual(PLAYS[0]["import_playbook"], "../99-utils/confirm-destroy.yml")
        self.assertEqual(PLAYS[1]["import_playbook"], "../99-utils/kube-connect.yml")
        preflight = PLAYS[2]
        for task in preflight["tasks"]:
            self.assertTrue("kubernetes.core.k8s_info" in task or "ansible.builtin.assert" in task)
        for play in PLAYS[2:]:
            self.assertTrue(play["any_errors_fatal"])
        for task in TASKS.values():
            self.assertNotIn("ignore_errors", task)
            self.assertNotIn("failed_when", task)
        destroy = TASKS["Destroy Terraform-managed realm resources"]
        self.assertEqual(destroy["when"], "tfstate_file.stat.exists")
        self.assertEqual(destroy["community.general.terraform"]["state"], "absent")

    def test_discovery_must_succeed_even_when_resources_are_absent(self):
        for name in ("Require successful CRD discovery", "Require successful custom resource discovery",
                     "Require successful RBAC discovery"):
            with self.subTest(name=name):
                self.assertTrue(accepts(name, {"api_found": True, "resources": []}))
                self.assertFalse(accepts(name, {"api_found": False, "resources": []}))
                self.assertFalse(accepts(name, {"resources": []}))

    def test_absent_crds_do_not_trigger_custom_resource_queries(self):
        task = TASKS["Inspect Keycloak custom resources across all namespaces"]
        reply = {"results": [{"api_found": True, "resources": []} for _ in range(4)]}
        self.assertEqual(evaluate(task["loop"], kc_crd_definitions=reply), [])
        self.assertNotIn("namespace", task["kubernetes.core.k8s_info"])
        definition = {"spec": {"group": "k8s.keycloak.org", "names": {"kind": "Keycloak"},
                               "versions": [{"name": "v2beta1", "storage": True},
                                            {"name": "v2alpha1", "storage": False}]}}
        reply["results"][0]["resources"] = [definition]
        self.assertEqual(evaluate(task["loop"], kc_crd_definitions=reply), [definition])
        templar = Templar(variables={"item": definition})
        self.assertEqual(templar.template(trust_as_template(task["kubernetes.core.k8s_info"]["api_version"])),
                         "k8s.keycloak.org/v2beta1")

    def test_only_the_exact_helm_owned_keycloak_is_allowed(self):
        name = "Refuse custom resources outside the main Helm release"
        self.assertTrue(accepts(name, owned_keycloak()))
        changes = [
            ("kind", "KeycloakRealmImport"), ("kind", "KeycloakOIDCClient"),
            ("kind", "KeycloakSAMLClient"), ("namespace", "other"),
            ("name", "another-keycloak"), ("release-name", "another-release"),
            ("release-namespace", "other"), ("annotations", {}),
        ]
        for field, value in changes:
            resource = owned_keycloak()
            if field == "kind":
                resource[field] = value
            elif field.startswith("release-"):
                resource["metadata"]["annotations"]["meta.helm.sh/" + field] = value
            else:
                resource["metadata"][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(accepts(name, resource))

    def test_only_local_operator_rbac_consumers_are_allowed(self):
        name = "Refuse shared operator RBAC consumers"
        for resource in (binding(), binding(subject_namespace="identity"), binding("ClusterRoleBinding")):
            self.assertTrue(accepts(name, resource))
        rejected = [binding(namespace="other"), binding(subject_namespace="other"),
                    binding("ClusterRoleBinding", namespace="other")]
        for field, value in [("name", "someone-else"), ("kind", "User")]:
            resource = binding()
            resource["subjects"][0][field] = value
            rejected.append(resource)
        resource = binding("ClusterRoleBinding")
        resource["metadata"]["name"] = "another-binding"
        rejected.append(resource)
        resource = binding()
        resource["subjects"].append({"kind": "User", "name": "other"})
        rejected.append(resource)
        resource = binding()
        del resource["subjects"]
        rejected.append(resource)
        for resource in rejected:
            with self.subTest(resource=resource):
                self.assertFalse(accepts(name, resource))

    def test_rbac_queries_are_global_and_only_shared_roles_are_selected(self):
        query = TASKS["Inspect consumers of the shared operator roles"]
        self.assertEqual(set(query["loop"]), {"RoleBinding", "ClusterRoleBinding"})
        self.assertNotIn("namespace", query["kubernetes.core.k8s_info"])
        roles = PLAYS[2]["vars"]["keycloak_operator_roles"]
        resources = []
        for role in roles:
            resource = binding()
            resource["roleRef"]["name"] = role
            resources.append(resource)
        unrelated = deepcopy(resources[0])
        unrelated["roleRef"]["name"] = "view"
        namespaced_role = deepcopy(resources[0])
        namespaced_role["roleRef"]["kind"] = "Role"
        results = {"results": [{"resources": resources + [unrelated, namespaced_role]}]}
        selected = evaluate(TASKS["Refuse shared operator RBAC consumers"]["loop"],
                            kc_role_bindings=results, keycloak_operator_roles=roles)
        self.assertEqual(selected, resources)

    def test_resource_contents_are_not_logged(self):
        for name in ("Inspect Keycloak custom resources across all namespaces",
                     "Require successful custom resource discovery",
                     "Refuse custom resources outside the main Helm release",
                     "Wait for all Keycloak custom resources to disappear"):
            self.assertTrue(TASKS[name]["no_log"])

    def test_operator_stays_until_cr_cleanup_finishes(self):
        names = list(TASKS)
        wait = "Wait for all Keycloak custom resources to disappear"
        self.assertLess(names.index("Uninstall Keycloak main Helm release"), names.index(wait))
        self.assertLess(names.index(wait), names.index("Remove Keycloak Operator manifests"))
        predicate = TASKS[wait]["until"]
        for reply, expected in [({"api_found": True, "resources": []}, True),
                                ({"api_found": True, "resources": [owned_keycloak()]}, False),
                                ({"api_found": False, "resources": []}, False),
                                ({"resources": []}, False)]:
            self.assertEqual(bool(evaluate(predicate, kc_remaining_resources=reply)), expected)

    def test_cleanup_wait_only_runs_after_real_deletion(self):
        task = TASKS["Wait for all Keycloak custom resources to disappear"]
        for check_mode, should_wait in [(False, True), (True, False)]:
            with self.subTest(check_mode=check_mode):
                self.assertEqual(evaluate(task["when"], ansible_check_mode=check_mode), should_wait)
        # Check mode must still inspect ownership before predicting destruction.
        for task in PLAYS[2]["tasks"]:
            self.assertNotIn("when", task)

    def test_deletes_exact_reverse_standup_manifests_and_no_pvc_sweep(self):
        standup = yaml.safe_load((LAYER / "standup.yml").read_text())
        install = next(task for play in standup for task in play.get("tasks", [])
                       if task.get("name") == "Apply Keycloak Operator manifests")
        remove = TASKS["Remove Keycloak Operator manifests"]
        self.assertEqual(remove["loop"], list(reversed(install["loop"])))
        self.assertIn("--ignore-not-found", remove["ansible.builtin.command"]["cmd"])
        kinds = [task.get("kubernetes.core.k8s", {}).get("kind") for task in TASKS.values()]
        self.assertNotIn("PersistentVolumeClaim", kinds)
        self.assertEqual(TASKS["Remove Keycloak namespace"]["kubernetes.core.k8s"]["state"], "absent")


if __name__ == "__main__":
    unittest.main()
