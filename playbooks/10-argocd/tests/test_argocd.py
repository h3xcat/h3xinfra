"""Render the deployed upstream chart; never connect to a Kubernetes cluster."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


LAYER = Path(__file__).resolve().parents[1]
ROOT = LAYER.parents[1]


AGE_SECRET = "h3xinfra-argocd-sops-age"
KSOPS_VALUE_KEYS = {"env", "volumes", "volumeMounts", "initContainers"}


def helm_play(plays):
    return next(play for play in plays if play.get("name") == "Deploy Argo CD via Helm")


def task_named(play, name):
    return next(task for task in play["tasks"] if task["name"] == name)


def ansible_env(directory):
    config = Path(directory) / "ansible.cfg"
    config.write_text("[defaults]\n")
    return dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR="1")


def template_values(cases):
    """Let Ansible template the Helm task's values, with the play vars, filters
    and defaults of the real play. Returns {case: values}."""
    play = helm_play(yaml.safe_load((LAYER / "standup.yml").read_text()))
    values = task_named(play, "Deploy Argo CD via Helm")["kubernetes.core.helm"]["values"]
    versions = yaml.safe_load((ROOT / "inventory/production/group_vars/all/versions.yml").read_text())
    with tempfile.TemporaryDirectory(prefix="h3x-argocd-values-") as directory:
        playbook = Path(directory) / "values.yml"
        playbook.write_text(yaml.safe_dump([{
            "name": name, "hosts": "localhost", "gather_facts": False,
            "vars": {**play.get("vars", {}), "argocd": config, "domain_name": "example.com",
                     "image_versions": versions["image_versions"], "h3x_test_values": values},
            "tasks": [{"ansible.builtin.copy": {
                "content": "{{ h3x_test_values | to_json }}", "dest": f"{directory}/{name}.json", "mode": "0600",
            }}],
        } for name, config in cases.items()]))
        result = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)],
                                env=ansible_env(directory), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return {name: json.loads((Path(directory) / f"{name}.json").read_text()) for name in cases}


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
        absent = copy.deepcopy(config)
        absent.pop("ksops", None)
        enabled = copy.deepcopy(config)
        enabled["ksops"] = {"enabled": True}
        renamed = copy.deepcopy(config)
        renamed["ksops"] = {"enabled": "true", "age_secret_name": "custom-age"}
        cls.values = template_values({"example": config, "absent": absent, "enabled": enabled, "renamed": renamed})

        # The example values with every KSOPS key removed: the values from
        # before KSOPS existed. Disabled KSOPS must render byte-identically.
        before = copy.deepcopy(cls.values["example"])
        cls.disabled_ksops_values = (before.pop("repoServer"), before["configs"]["cm"].pop("kustomize.buildOptions"))
        cls.values["before"] = before

        plays = yaml.safe_load((LAYER / "standup.yml").read_text())
        cls.helm_task = task_named(helm_play(plays), "Deploy Argo CD via Helm")

        def render(values):
            return subprocess.run([
                "helm", "template", "h3xinfra-argocd-main", chart, "--namespace", "argocd",
                "-f", str(LAYER / "argocd-values.yaml"), "-f", "-",
            ], input=yaml.safe_dump(values), check=True, capture_output=True, text=True, timeout=120).stdout

        cls.rendered = {name: render(cls.values[name]) for name in ("example", "absent", "before", "enabled")}
        cls.docs = cls.load(cls.rendered["example"])
        cls.enabled_docs = cls.load(cls.rendered["enabled"])

    @staticmethod
    def load(text):
        return [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict)]

    def resource(self, kind, name, docs=None):
        return next(doc for doc in (self.docs if docs is None else docs)
                    if doc["kind"] == kind and doc["metadata"]["name"] == name)

    def repo_server_pod(self):
        return self.resource("Deployment", "h3xinfra-argocd-main-repo-server", self.enabled_docs)["spec"]["template"]["spec"]

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

    def test_disabled_ksops_renders_as_before(self):
        repo_server, build_options = self.disabled_ksops_values
        self.assertEqual(repo_server, {key: [] for key in KSOPS_VALUE_KEYS})
        self.assertEqual(build_options, "")
        self.assertEqual(self.rendered["example"], self.rendered["before"])
        self.assertEqual(self.rendered["absent"], self.rendered["before"])
        self.assertNotIn("ksops", self.rendered["example"].lower())
        self.assertNotIn("kustomize.buildOptions", self.rendered["example"])

    def test_ksops_uses_bundled_kustomize_with_exec_functions(self):
        cm = self.resource("ConfigMap", "argocd-cm", self.enabled_docs)["data"]
        self.assertEqual(cm["kustomize.buildOptions"], "--enable-alpha-plugins --enable-exec")
        pod = self.repo_server_pod()
        versions = yaml.safe_load((ROOT / "inventory/production/group_vars/all/versions.yml").read_text())
        init = next(c for c in pod["initContainers"] if c["name"] == "install-ksops")
        self.assertEqual(init["image"], f"quay.io/viaductoss/ksops:{versions['image_versions']['ksops']}")
        self.assertEqual(init["command"], ["/usr/local/bin/ksops", "install", "/custom-tools"])
        self.assertEqual(init["volumeMounts"], [{"name": "ksops-tools", "mountPath": "/custom-tools"}])
        main = next(c for c in pod["containers"] if c["name"] == "repo-server")
        mounts = {mount["mountPath"]: mount for mount in main["volumeMounts"]}
        self.assertEqual(mounts["/usr/local/bin/ksops"], {
            "name": "ksops-tools", "mountPath": "/usr/local/bin/ksops", "subPath": "ksops", "readOnly": True,
        })
        self.assertNotIn("/usr/local/bin/kustomize", mounts)
        self.assertEqual(mounts["/app/config/sops-age"], {
            "name": "ksops-age-key", "mountPath": "/app/config/sops-age", "readOnly": True,
        })
        self.assertIn({"name": "SOPS_AGE_KEY_FILE", "value": "/app/config/sops-age/keys.txt"}, main["env"])
        volumes = {volume["name"]: volume for volume in pod["volumes"]}
        self.assertEqual(volumes["ksops-tools"]["emptyDir"], {"sizeLimit": "256Mi"})
        self.assertEqual(volumes["ksops-age-key"]["secret"], {
            "secretName": AGE_SECRET, "optional": False, "items": [{"key": "keys.txt", "path": "keys.txt"}],
        })

    def test_ksops_secret_name_and_enabled_flag(self):
        values = self.values["renamed"]
        secret = next(volume for volume in values["repoServer"]["volumes"] if "secret" in volume)
        self.assertEqual(secret["secret"]["secretName"], "custom-age")
        self.assertEqual(values["configs"]["cm"]["kustomize.buildOptions"], "--enable-alpha-plugins --enable-exec")

    def test_age_key_reaches_only_the_repo_server_container(self):
        workloads = [doc for doc in self.enabled_docs if doc["kind"] in {"Deployment", "StatefulSet", "Job"}]
        holders = []
        for workload in workloads:
            pod = workload["spec"]["template"]["spec"]
            keyed = {volume["name"] for volume in pod.get("volumes", [])
                     if (volume.get("secret") or {}).get("secretName") == AGE_SECRET}
            for container in pod.get("containers", []) + pod.get("initContainers", []):
                refs = [mount for mount in container.get("volumeMounts", []) if mount["name"] in keyed]
                refs += [env for env in container.get("env", []) + container.get("envFrom", [])
                         if AGE_SECRET in json.dumps(env)]
                if refs:
                    holders.append((workload["metadata"]["name"], container["name"]))
        self.assertEqual(holders, [("h3xinfra-argocd-main-repo-server", "repo-server")])
        self.assertEqual(self.rendered["enabled"].count(AGE_SECRET), 1)

    def test_enabled_workloads_pass_restricted_pod_security(self):
        workloads = [doc for doc in self.enabled_docs if doc["kind"] in {"Deployment", "StatefulSet", "Job"}]
        for workload in workloads:
            pod = workload["spec"]["template"]["spec"]
            pod_context = pod.get("securityContext") or {}
            self.assertFalse(any("hostPath" in volume for volume in pod.get("volumes", [])))
            for container in pod.get("containers", []) + pod.get("initContainers", []):
                with self.subTest(workload=workload["metadata"]["name"], container=container["name"]):
                    context = container.get("securityContext") or {}
                    self.assertTrue(context.get("runAsNonRoot", pod_context.get("runAsNonRoot")))
                    self.assertNotEqual(context.get("runAsUser", pod_context.get("runAsUser")), 0)
                    self.assertIs(context.get("allowPrivilegeEscalation"), False)
                    self.assertFalse(context.get("privileged", False))
                    self.assertIn("ALL", context["capabilities"]["drop"])
                    self.assertLessEqual(set(context["capabilities"].get("add", [])), {"NET_BIND_SERVICE"})
                    seccomp = context.get("seccompProfile", pod_context.get("seccompProfile")) or {}
                    self.assertIn(seccomp.get("type"), {"RuntimeDefault", "Localhost"})
        init = next(c for c in self.repo_server_pod()["initContainers"] if c["name"] == "install-ksops")
        self.assertIs(init["securityContext"]["readOnlyRootFilesystem"], True)
        self.assertEqual(init["securityContext"]["runAsUser"], 65532)


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

    def test_ksops_requires_the_age_key_secret(self):
        # The registered k8s_info result is mocked; no cluster is contacted.
        play = helm_play(yaml.safe_load((LAYER / "standup.yml").read_text()))
        read, check = task_named(play, "Read the KSOPS age key Secret"), task_named(play, "Validate KSOPS configuration")
        self.assertTrue(read["no_log"])
        self.assertEqual(read["register"], "argocd_ksops_age_secret")
        self.assertNotIn("no_log", check)
        present = {"resources": [{"data": {"keys.txt": "QUdFLVNFQ1JFVC1LRVktdGVzdA=="}}]}
        cases = [
            ("disabled", {"argocd": {"namespace": "argocd"}}, True),
            ("missing", {"argocd": {"namespace": "argocd", "ksops": {"enabled": True}},
                         "argocd_ksops_age_secret": {"resources": []}}, False),
            ("no key", {"argocd": {"namespace": "argocd", "ksops": {"enabled": True}},
                        "argocd_ksops_age_secret": {"resources": [{"data": {"other": "eA=="}}]}}, False),
            ("no pin", {"argocd": {"namespace": "argocd", "ksops": {"enabled": True}},
                        "argocd_ksops_age_secret": present, "image_versions": {}}, False),
            ("present", {"argocd": {"namespace": "argocd", "ksops": {"enabled": True, "age_secret_name": "custom-age"}},
                         "argocd_ksops_age_secret": present}, True),
        ]
        with tempfile.TemporaryDirectory(prefix="h3x-argocd-ksops-") as directory:
            env = ansible_env(directory)
            for name, variables, passes in cases:
                with self.subTest(case=name):
                    playbook = Path(directory) / "ksops.yml"
                    playbook.write_text(yaml.safe_dump([{
                        "name": "Check KSOPS inputs", "hosts": "localhost", "gather_facts": False,
                        "vars": {**play["vars"], "image_versions": {"ksops": "v0.0.0"}, **variables},
                        "tasks": [check],
                    }]))
                    result = subprocess.run([
                        "ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook),
                    ], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
                    output = result.stdout + result.stderr
                    self.assertNotIn("QUdFLVNFQ1JFVC1LRVktdGVzdA==", output)
                    if passes:
                        self.assertEqual(result.returncode, 0, output)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        secret = variables["argocd"]["ksops"].get("age_secret_name", AGE_SECRET)
                        self.assertIn(f"Secret argocd/{secret} with the age identity in key keys.txt", output)

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
