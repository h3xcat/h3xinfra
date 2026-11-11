"""Render the deployed upstream chart; never connect to a Kubernetes cluster."""

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
ROOT = LAYER.parents[1]


class ArgoCDTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="h3x-argocd-test-")
        cls.addClassCleanup(cls.work.cleanup)
        chart = os.environ.get("ARGOCD_CHART_DIR")
        if not chart:
            version = yaml.safe_load((ROOT / "inventory/production/group_vars/all/versions.yml").read_text())["chart_versions"]["argocd"]
            env = dict(os.environ, HELM_REPOSITORY_CONFIG=f"{cls.work.name}/repositories.yaml",
                       HELM_REPOSITORY_CACHE=f"{cls.work.name}/cache")
            subprocess.run([
                "helm", "pull", "argo-cd", "--repo", "https://argoproj.github.io/argo-helm",
                "--version", version, "--untar", "--untardir", cls.work.name,
            ], env=env, check=True, capture_output=True, text=True, timeout=120)
            chart = f"{cls.work.name}/argo-cd"

        config = yaml.safe_load((ROOT / "inventory/production/host_vars/k8s/argocd.yml").read_text())["argocd"]
        config["hostname"] = "argocd.app.example.com"
        config["oidc"]["issuer"] = "https://keycloak.app.example.com/realms/h3xinfra"
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["to_json"] = json.dumps

        def resolve(value):
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if isinstance(value, str):
                return env.from_string(value).render(argocd=config)
            return value

        plays = yaml.safe_load((LAYER / "standup.yml").read_text())
        task = next(task for play in plays for task in play.get("tasks", [])
                    if task["name"] == "Deploy Argo CD via Helm")
        cls.helm_task = task
        result = subprocess.run([
            "helm", "template", "h3xinfra-argocd-main", chart, "--namespace", "argocd",
            "-f", str(LAYER / "argocd-values.yaml"), "-f", "-",
        ], input=yaml.safe_dump(resolve(task["kubernetes.core.helm"]["values"])),
            check=True, capture_output=True, text=True, timeout=120)
        cls.docs = [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]

    def resource(self, kind, name):
        return next(doc for doc in self.docs if doc["kind"] == kind and doc["metadata"]["name"] == name)

    def test_https_route_and_clusterip(self):
        route = self.resource("HTTPRoute", "h3xinfra-argocd-main-server")
        self.assertEqual(route["spec"]["hostnames"], ["argocd.app.example.com"])
        self.assertEqual(route["spec"]["parentRefs"], [{
            "name": "h3xinfra-gateway-pre-shared", "namespace": "envoy-gateway-system", "sectionName": "https-app",
        }])
        backend = route["spec"]["rules"][0]["backendRefs"][0]
        service = self.resource("Service", backend["name"])
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(backend["port"], 80)
        self.assertFalse(any(doc["kind"] in {"Ingress", "Certificate", "SecurityPolicy", "GRPCRoute"} for doc in self.docs))

    def test_native_pkce_sso_and_rbac(self):
        cm = self.resource("ConfigMap", "argocd-cm")["data"]
        oidc = yaml.safe_load(cm["oidc.config"])
        self.assertEqual(oidc["issuer"], "https://keycloak.app.example.com/realms/h3xinfra")
        self.assertIs(oidc["enablePKCEAuthentication"], True)
        self.assertEqual(oidc["clientID"], "argocd")
        self.assertNotIn("clientSecret", oidc)
        self.assertEqual(cm["admin.enabled"], "false")
        self.assertEqual(cm["users.anonymous.enabled"], "false")
        secret = self.resource("Secret", "argocd-secret")
        self.assertNotIn("oidc.keycloak.clientSecret", secret.get("data") or {})
        rbac = self.resource("ConfigMap", "argocd-rbac-cm")["data"]
        self.assertEqual(rbac["policy.default"], "")
        self.assertIn("g, argocd-admins, role:admin", rbac["policy.csv"])
        self.assertTrue(self.helm_task["no_log"])

    def test_only_gateway_can_reach_server(self):
        default_deny = self.resource("NetworkPolicy", "h3xinfra-argocd-main-default-deny")
        self.assertEqual(default_deny["spec"], {"podSelector": {}, "policyTypes": ["Ingress"]})
        server = self.resource("Deployment", "h3xinfra-argocd-main-server")
        labels = server["spec"]["template"]["metadata"]["labels"]
        policies = [doc for doc in self.docs if doc["kind"] == "NetworkPolicy"
                    and all(labels.get(k) == v for k, v in doc["spec"]["podSelector"].get("matchLabels", {}).items())]
        rules = [rule for policy in policies for rule in policy["spec"].get("ingress", [])]
        self.assertEqual(len(rules), 1, "An additive allow-all policy would bypass Gateway isolation")
        self.assertEqual(rules[0]["ports"], [{"protocol": "TCP", "port": 8080}])
        peer = rules[0]["from"][0]
        self.assertEqual(peer["namespaceSelector"]["matchLabels"], {"kubernetes.io/metadata.name": "envoy-gateway-system"})
        self.assertEqual(peer["podSelector"]["matchLabels"]["gateway.envoyproxy.io/owning-gateway-name"], "h3xinfra-gateway-pre-shared")

    def test_workloads_and_init_containers_have_budgets(self):
        workloads = [doc for doc in self.docs if doc["kind"] in {"Deployment", "StatefulSet", "Job"}]
        self.assertGreaterEqual(len(workloads), 6)
        for workload in workloads:
            pod = workload["spec"]["template"]["spec"]
            for container in pod.get("containers", []) + pod.get("initContainers", []):
                with self.subTest(workload=workload["metadata"]["name"], container=container["name"]):
                    for bound in ("requests", "limits"):
                        self.assertTrue({"cpu", "memory", "ephemeral-storage"} <= container["resources"][bound].keys())

    def test_crds_retained_and_no_apps_adopted(self):
        crds = [doc for doc in self.docs if doc["kind"] == "CustomResourceDefinition"]
        self.assertGreaterEqual(len(crds), 3)
        for crd in crds:
            self.assertEqual(crd["metadata"]["annotations"]["helm.sh/resource-policy"], "keep")
        self.assertFalse(any(doc["kind"] in {"Application", "ApplicationSet", "PersistentVolumeClaim"} for doc in self.docs))
        teardown = yaml.safe_load((LAYER / "teardown.yml").read_text())
        tasks = [task for play in teardown for task in play.get("tasks", [])]
        self.assertTrue(all("kubernetes.core.k8s" not in task for task in tasks))


class ConfigurationTests(unittest.TestCase):
    def test_validation_reports_missing_configuration(self):
        plays = yaml.safe_load((LAYER / "standup.yml").read_text())
        task = plays[1]["tasks"][0]
        valid = yaml.safe_load((ROOT / "inventory/production/host_vars/k8s/argocd.yml").read_text())
        with tempfile.TemporaryDirectory(prefix="h3x-argocd-validation-") as directory:
            config = Path(directory) / "ansible.cfg"
            config.write_text("[defaults]\n")
            env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR="1")
            cases = [{}, {"argocd": {}}, {"argocd": {"namespace": "argocd"}}, valid]
            for variables in cases:
                with self.subTest(variables=variables):
                    playbook = Path(directory) / "validation.yml"
                    playbook.write_text(yaml.safe_dump([{
                        "name": "Check Argo CD inputs", "hosts": "localhost", "gather_facts": False,
                        "vars": {"domain_name": "example.com", **variables}, "tasks": [task],
                    }]))
                    result = subprocess.run([
                        "ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook),
                    ], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
                    output = result.stdout + result.stderr
                    if variables is valid:
                        self.assertEqual(result.returncode, 0, output)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(task["ansible.builtin.assert"]["fail_msg"], output)
                        self.assertNotIn("censored", output)

    def test_keycloak_client_plans(self):
        # Copy just the client definitions: no deployment tfvars, backend,
        # state or real provider calls can enter this mocked-provider test.
        source = ROOT / "playbooks/10-keycloak/terraform"
        with tempfile.TemporaryDirectory(prefix="h3x-argocd-clients-") as directory:
            target = Path(directory)
            for name in ("providers.tf", "variables.tf", "clients.tf", "realm.tf", ".terraform.lock.hcl"):
                shutil.copyfile(source / name, target / name)
            (target / "tests").mkdir()
            shutil.copyfile(LAYER / "tests/oidc_clients.tftest.hcl", target / "tests/oidc_clients.tftest.hcl")
            env = dict(os.environ, TF_DATA_DIR=str(target / ".terraform"), TF_IN_AUTOMATION="1", TF_INPUT="0")
            for arguments in (["init", "-backend=false", "-lockfile=readonly"], ["test"]):
                result = subprocess.run(["terraform", *arguments, "-no-color"], cwd=target, env=env,
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
