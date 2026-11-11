"""Render the pinned upstream chart and run the layer guards locally.

Nothing here connects to a Kubernetes cluster. Ansible runs only on localhost
with a stub ansible.cfg, and the chart is rendered with `helm template`.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml


LAYER = Path(__file__).resolve().parents[1]
ROOT = LAYER.parents[1]
CHART_REPO = "https://bitnami.github.io/sealed-secrets"
IMAGE_REPO = "docker.io/bitnami/sealed-secrets-controller"
KEY_LABEL = "sealedsecrets.bitnami.com/sealed-secrets-key"
FAKE_KEY = "FAKE-PRIVATE-KEY-MATERIAL-MUST-NOT-BE-PRINTED"

VERSIONS = yaml.safe_load((ROOT / "inventory/production/group_vars/all/versions.yml").read_text())
CONFIG = yaml.safe_load((ROOT / "inventory/production/host_vars/k8s/sealed_secrets.yml").read_text())["sealed_secrets"]


class InventoryLoader(yaml.SafeLoader):
    """SafeLoader that reads Ansible Vault values as opaque strings."""


InventoryLoader.add_constructor("!vault", lambda loader, node: loader.construct_scalar(node))


def load(path):
    return yaml.load(Path(path).read_text(), Loader=InventoryLoader)


def layer_play(name="standup.yml"):
    plays = load(LAYER / name)
    return next(play for play in plays if play.get("hosts") == "k8s")


def task(play, name):
    return next(task for task in play["tasks"] if task["name"] == name)


def run_play(directory, tasks, variables, verbose=False):
    """Run tasks on localhost with a stub ansible.cfg; return (rc, output)."""
    directory = Path(directory)
    config = directory / "ansible.cfg"
    config.write_text("[defaults]\n")
    playbook = directory / "play.yml"
    playbook.write_text(yaml.safe_dump([{
        "name": "Local check", "hosts": "localhost", "gather_facts": False,
        "vars": variables, "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_NOCOLOR="1", ANSIBLE_FORCE_COLOR="0")
    command = ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)]
    if verbose:
        command.append("-v")
    result = subprocess.run(command, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=60)
    return result.returncode, result.stdout + result.stderr


def play_vars(**extra):
    """Inventory-shaped variables plus the play vars of standup.yml."""
    return {
        "sealed_secrets": CONFIG,
        "chart_versions": VERSIONS["chart_versions"],
        "image_versions": VERSIONS["image_versions"],
        **layer_play()["vars"],
        **extra,
    }


class RenderTests(unittest.TestCase):
    """The release Ansible deploys, rendered from the pinned chart."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="h3x-sealed-secrets-test-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        chart = os.environ.get("SEALED_SECRETS_CHART_DIR")
        if not chart:
            env = dict(os.environ, HELM_REPOSITORY_CONFIG=str(work / "repositories.yaml"),
                       HELM_REPOSITORY_CACHE=str(work / "cache"))
            subprocess.run([
                "helm", "pull", "sealed-secrets", "--repo", CHART_REPO,
                "--version", VERSIONS["chart_versions"]["sealed_secrets"],
                "--untar", "--untardir", str(work),
            ], env=env, check=True, capture_output=True, text=True, timeout=120)
            chart = str(work / "sealed-secrets")
        cls.chart = Path(chart)

        # Ansible itself templates the Helm values, with the play vars.
        cls.helm_task = task(layer_play(), "Deploy the Sealed Secrets controller via Helm")
        rendered = work / "values.json"
        rc, output = run_play(work, [{
            "name": "Template the Helm values",
            "ansible.builtin.copy": {"content": "{{ h3x_values | to_nice_json }}", "dest": str(rendered)},
        }], play_vars(h3x_values=cls.helm_task["kubernetes.core.helm"]["values"]))
        if rc != 0:
            raise AssertionError(output)
        cls.values = json.loads(rendered.read_text())
        cls.namespace = CONFIG["namespace"]
        cls.release = f"{CONFIG['release_name_prefix']}-main"
        result = subprocess.run([
            "helm", "template", cls.release, str(cls.chart), "--namespace", cls.namespace,
            "-f", str(LAYER / "sealed-secrets-values.yaml"), "-f", str(rendered),
        ], check=True, capture_output=True, text=True, timeout=120)
        cls.docs = [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]
        cls.deployment = cls.resource("Deployment", cls.release)
        cls.pod = cls.deployment["spec"]["template"]["spec"]
        cls.container = cls.pod["containers"][0]

    @classmethod
    def resource(cls, kind, name):
        return next(doc for doc in cls.docs if doc["kind"] == kind and doc["metadata"]["name"] == name)

    def kinds(self, kind):
        return [doc for doc in self.docs if doc["kind"] == kind]

    def test_controller_never_generates_its_own_key(self):
        args = self.container["args"]
        self.assertEqual(args[args.index("--key-renew-period") + 1], "0")
        self.assertEqual(args[args.index("--key-prefix") + 1], CONFIG["key_secret_name"])
        self.assertNotIn("--key-cutoff-time", args)
        # The chart must never ship a key (or any other Secret).
        self.assertEqual(self.kinds("Secret"), [])

    def test_image_is_pinned_by_tag_and_digest(self):
        pin = VERSIONS["image_versions"]["sealed_secrets_controller"]
        self.assertRegex(pin, r"^[\w][\w.-]*@sha256:[0-9a-f]{64}$")
        self.assertEqual(self.container["image"], f"{IMAGE_REPO}:{pin}")
        self.assertEqual(self.values["image"]["tag"], pin)
        chart = load(self.chart / "Chart.yaml")
        self.assertEqual(chart["version"], VERSIONS["chart_versions"]["sealed_secrets"])
        self.assertEqual(pin.split("@")[0], str(chart["appVersion"]),
                         "Bump the image together with the chart: the chart appVersion changed")

    def test_restricted_pod_security(self):
        pod_context = self.pod["securityContext"]
        self.assertEqual(pod_context["seccompProfile"], {"type": "RuntimeDefault"})
        context = self.container["securityContext"]
        self.assertIs(context["runAsNonRoot"], True)
        self.assertNotEqual(context["runAsUser"], 0)
        self.assertIs(context["readOnlyRootFilesystem"], True)
        self.assertIs(context["allowPrivilegeEscalation"], False)
        self.assertEqual(context["capabilities"], {"drop": ["ALL"]})
        self.assertEqual(context["seccompProfile"], {"type": "RuntimeDefault"})
        self.assertNotIn("privileged", context)
        for field in ("hostNetwork", "hostPID", "hostIPC"):
            self.assertFalse(self.pod.get(field, False), field)
        for port in self.container["ports"]:
            self.assertNotIn("hostPort", port)
        self.assertTrue(all("hostPath" not in volume for volume in self.pod["volumes"]))

    def test_resources_and_priority(self):
        for bound in ("requests", "limits"):
            self.assertTrue({"cpu", "memory", "ephemeral-storage"} <= self.container["resources"][bound].keys(), bound)
        self.assertEqual(self.pod["priorityClassName"], "h3xinfra-platform-critical")
        self.assertEqual(self.deployment["metadata"]["namespace"], self.namespace)

    def test_default_deny_ingress_only(self):
        policies = self.kinds("NetworkPolicy")
        self.assertEqual([policy["metadata"]["name"] for policy in policies], [f"{self.release}-default-deny"],
                         "An additive allow policy (such as the chart's own) would open the controller")
        self.assertEqual(policies[0]["metadata"]["namespace"], self.namespace)
        self.assertEqual(policies[0]["spec"], {"podSelector": {}, "policyTypes": ["Ingress"]})
        for kind in ("Ingress", "ServiceMonitor", "PrometheusRule", "PodSecurityPolicy", "PodDisruptionBudget"):
            self.assertEqual(self.kinds(kind), [], kind)
        for service in self.kinds("Service"):
            self.assertEqual(service["spec"]["type"], "ClusterIP")

    def test_rbac_scope(self):
        # A chart bump that widens the controller's permissions fails here,
        # so the reviewer sees it. Update README.md together with this list.
        cluster_roles = self.kinds("ClusterRole")
        self.assertEqual([role["metadata"]["name"] for role in cluster_roles], [f"{self.release}-unsealer"])
        rules = {(tuple(rule["apiGroups"]), tuple(rule["resources"])): sorted(rule["verbs"])
                 for rule in cluster_roles[0]["rules"]}
        self.assertEqual(rules, {
            (("bitnami.com",), ("sealedsecrets",)): ["get", "list", "watch"],
            (("bitnami.com",), ("sealedsecrets/status",)): ["update"],
            (("",), ("secrets",)): ["create", "delete", "get", "list", "update", "watch"],
            (("",), ("events",)): ["create", "patch"],
        })
        account = self.pod["serviceAccountName"]
        for binding in self.kinds("ClusterRoleBinding") + self.kinds("RoleBinding"):
            with self.subTest(binding=binding["metadata"]["name"]):
                subjects = [(s["kind"], s["name"], s.get("namespace")) for s in binding["subjects"]]
                self.assertEqual(subjects, [("ServiceAccount", account, self.namespace)],
                                 "Only the controller may hold these roles (no service proxier for system:authenticated)")
        roles = self.kinds("Role")
        self.assertEqual([role["metadata"]["name"] for role in roles], [f"{self.release}-key-admin"])
        self.assertEqual(roles[0]["metadata"]["namespace"], self.namespace)

    def test_crds_stay_outside_the_release(self):
        self.assertIs(self.helm_task["kubernetes.core.helm"]["skip_crds"], True)
        self.assertEqual(self.kinds("CustomResourceDefinition"), [])

    def test_pinned_chart_passes_the_crd_check(self):
        crds = subprocess.run(["helm", "show", "crds", str(self.chart)], check=True,
                              capture_output=True, text=True, timeout=60).stdout
        check = task(layer_play(), "Check the rendered CRD")
        with tempfile.TemporaryDirectory(prefix="h3x-sealed-secrets-crd-") as directory:
            rc, output = run_play(directory, [check], {"sealed_secrets_crd_render": {"stdout": crds}})
        self.assertEqual(rc, 0, output)


class PlaybookTests(unittest.TestCase):
    """Task order, guards and their behaviour, run with local Ansible."""

    def setUp(self):
        self.play = layer_play()
        self.names = [task["name"] for task in self.play["tasks"]]
        self.directory = tempfile.TemporaryDirectory(prefix="h3x-sealed-secrets-play-")
        self.addCleanup(self.directory.cleanup)

    def test_key_check_runs_before_any_change(self):
        read = task(self.play, "Read the sealing key Secret")
        info = read["kubernetes.core.k8s_info"]
        self.assertEqual((info["kind"], info["api_version"]), ("Secret", "v1"))
        self.assertEqual(info["name"], "{{ sealed_secrets_key_secret_name }}")
        self.assertEqual(info["namespace"], "{{ sealed_secrets_namespace }}")
        self.assertIs(read["no_log"], True, "The k8s_info result holds the private key")
        require = task(self.play, "Require the operator-supplied sealing key")
        self.assertIn("provision the sealing key separately", require["ansible.builtin.assert"]["fail_msg"])
        self.assertIn("playbooks/10-sealed-secrets/README.md", require["ansible.builtin.assert"]["fail_msg"])
        first_change = min(self.names.index(name) for name in (
            "Add the Sealed Secrets Helm repository", "Create the Sealed Secrets namespace",
            "Apply the SealedSecret CRD", "Deploy the Sealed Secrets controller via Helm"))
        self.assertLess(self.names.index("Read the sealing key Secret"), self.names.index("Require the operator-supplied sealing key"))
        self.assertLess(self.names.index("Require the operator-supplied sealing key"), first_change)
        forget = task(self.play, "Forget the sealing key Secret")
        self.assertEqual(forget["ansible.builtin.set_fact"], {"sealed_secrets_key": {}})
        for name in ("Read the sealing key Secret", "Require the operator-supplied sealing key", "Forget the sealing key Secret"):
            self.assertNotIn("when", task(self.play, name), f"{name} must always run")
        self.assertLess(self.names.index("Forget the sealing key Secret"), first_change)

    def test_key_assert_behaviour(self):
        require = task(self.play, "Require the operator-supplied sealing key")
        message = require["ansible.builtin.assert"]["fail_msg"].split("This layer")[0].strip()

        def secret(**changes):
            base = {
                "type": "kubernetes.io/tls",
                "metadata": {"name": CONFIG["key_secret_name"], "labels": {KEY_LABEL: "active"}},
                "data": {"tls.crt": "Q0VSVA==", "tls.key": FAKE_KEY},
            }
            base.update(changes)
            return {"resources": [base]}

        cases = {
            "missing": ({"resources": []}, False),
            "opaque": (secret(type="Opaque"), False),
            "no labels": (secret(metadata={"name": CONFIG["key_secret_name"]}), False),
            "inactive label": (secret(metadata={"labels": {KEY_LABEL: "compromised"}}), False),
            "no private key": (secret(data={"tls.crt": "Q0VSVA=="}), False),
            "no data": (secret(data=None), False),
            "valid": (secret(), True),
        }
        for case, (result, ok) in cases.items():
            with self.subTest(case=case):
                rc, output = run_play(self.directory.name, [require], play_vars(sealed_secrets_key=result), verbose=True)
                self.assertNotIn(FAKE_KEY, output)
                if ok:
                    self.assertEqual(rc, 0, output)
                else:
                    self.assertNotEqual(rc, 0)
                    self.assertIn("provision the sealing key separately", output)
                    self.assertIn(f"Secret sealed-secrets/{CONFIG['key_secret_name']} must exist", " ".join(output.split()))
                    self.assertTrue(message)
                    self.assertNotIn("censored", output)

    def test_configuration_validation(self):
        validate = task(self.play, "Validate Sealed Secrets configuration")
        tag_only = dict(VERSIONS["image_versions"], sealed_secrets_controller="0.41.2")
        no_chart = {key: value for key, value in VERSIONS["chart_versions"].items() if key != "sealed_secrets"}
        cases = {
            "inventory": (play_vars(), True),
            "knob defaults": ({key: value for key, value in play_vars().items() if key != "sealed_secrets"}, True),
            "tag without digest": (play_vars(image_versions=tag_only), False),
            "no chart pin": (play_vars(chart_versions=no_chart), False),
            "bad namespace": (play_vars(sealed_secrets=dict(CONFIG, namespace="Bad_NS")), False),
            # Valid Secret names that the controller's --key-prefix rejects.
            "key name with a dot": (play_vars(sealed_secrets=dict(CONFIG, key_secret_name="sealing.key-2026")), False),
            "key name too long": (play_vars(sealed_secrets=dict(CONFIG, key_secret_name="k" * 246)), False),
            "key name at the limit": (play_vars(sealed_secrets=dict(CONFIG, key_secret_name="k" * 245)), True),
        }
        for case, (variables, ok) in cases.items():
            with self.subTest(case=case):
                rc, output = run_play(self.directory.name, [validate], variables)
                if ok:
                    self.assertEqual(rc, 0, output)
                else:
                    self.assertNotEqual(rc, 0)
                    self.assertIn("Set chart_versions.sealed_secrets", output)

    def test_crd_is_applied_outside_helm(self):
        render = task(self.play, "Render the SealedSecret CRD of the pinned chart")
        self.assertEqual(render["ansible.builtin.command"]["argv"][:4], ["helm", "show", "crds", "sealed-secrets/sealed-secrets"])
        self.assertIs(render["check_mode"], False)
        apply = task(self.play, "Apply the SealedSecret CRD")["kubernetes.core.k8s"]
        self.assertIs(apply["apply"], True)
        self.assertEqual(apply["server_side_apply"], {"field_manager": "h3xinfra-sealed-secrets"})
        self.assertLess(self.names.index("Apply the SealedSecret CRD"),
                        self.names.index("Deploy the Sealed Secrets controller via Helm"))
        check = task(self.play, "Check the rendered CRD")
        crd = {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
               "metadata": {"name": "sealedsecrets.bitnami.com"}}
        extra = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "surprise"}}
        for case, documents, ok in (("one CRD", [crd], True), ("extra object", [crd, extra], False),
                                    ("empty", [], False)):
            with self.subTest(case=case):
                text = "\n---\n".join(yaml.safe_dump(doc) for doc in documents)
                rc, output = run_play(self.directory.name, [check], {"sealed_secrets_crd_render": {"stdout": text}})
                self.assertEqual(rc == 0, ok, output)

    def test_teardown_keeps_crd_key_and_namespace(self):
        plays = load(LAYER / "teardown.yml")
        self.assertEqual(plays[0]["import_playbook"], "../99-utils/confirm-destroy.yml")
        tasks = layer_play("teardown.yml")["tasks"]
        self.assertEqual(len(tasks), 1)
        helm = tasks[0]["kubernetes.core.helm"]
        self.assertEqual(helm["state"], "absent")
        self.assertEqual(helm["name"], "{{ sealed_secrets_release }}")
        self.assertEqual(layer_play("teardown.yml")["vars"]["sealed_secrets_release"], self.play["vars"]["sealed_secrets_release"])


class WiringTests(unittest.TestCase):
    """Stack guards, version pins and Renovate tracking."""

    def import_entry(self, stack, layer_file):
        plays = load(ROOT / "playbooks" / stack)
        index = next(i for i, play in enumerate(plays) if play.get("import_playbook") == layer_file)
        return index, plays

    def test_stack_imports_are_guarded(self):
        index, plays = self.import_entry("stack-standup.yml", "10-sealed-secrets/standup.yml")
        argocd = next(i for i, play in enumerate(plays) if play.get("import_playbook") == "10-argocd/standup.yml")
        self.assertLess(index, argocd, "SealedSecrets must be decryptable before Argo CD syncs them")
        condition = plays[index]["when"]
        self.assertIn("sealed_secrets.enabled", condition)
        teardown_index, teardown = self.import_entry("stack-teardown.yml", "10-sealed-secrets/teardown.yml")
        self.assertIn("sealed_secrets.enabled", teardown[teardown_index]["when"])
        with tempfile.TemporaryDirectory(prefix="h3x-sealed-secrets-when-") as directory:
            check = [{"name": "Evaluate the import condition",
                      "ansible.builtin.assert": {"that": [condition], "fail_msg": "CONDITION-FALSE", "quiet": True}}]
            cases = {"unset": ({}, False), "disabled": ({"sealed_secrets": CONFIG}, False),
                     "enabled": ({"sealed_secrets": dict(CONFIG, enabled=True)}, True)}
            self.assertIs(CONFIG["enabled"], False, "The layer is opt-in")
            for case, (variables, enabled) in cases.items():
                with self.subTest(case=case):
                    rc, output = run_play(directory, check, variables)
                    if enabled:
                        self.assertEqual(rc, 0, output)
                    else:
                        self.assertIn("CONDITION-FALSE", output)

    def test_versions_are_mirrored_in_the_example_inventory(self):
        example = load(ROOT / "inventory.example.yml")["all"]["vars"]
        self.assertEqual(example["chart_versions"]["sealed_secrets"], VERSIONS["chart_versions"]["sealed_secrets"])
        self.assertEqual(example["image_versions"]["sealed_secrets_controller"],
                         VERSIONS["image_versions"]["sealed_secrets_controller"])

    def test_renovate_tracks_tag_and_digest_and_never_automerges(self):
        config = json.loads((ROOT / "renovate.json").read_text())
        manager = config["customManagers"][0]
        pattern = re.compile(manager["matchStrings"][0].replace("(?<", "(?P<"))
        text = (ROOT / "inventory/production/group_vars/all/versions.yml").read_text()
        captures = {match["depName"]: match for match in pattern.finditer(text)}
        tag, digest = VERSIONS["image_versions"]["sealed_secrets_controller"].split("@")
        image = captures[IMAGE_REPO]
        self.assertEqual((image["datasource"], image["currentValue"], image["currentDigest"]), ("docker", tag, digest))
        chart = captures["sealed-secrets"]
        self.assertEqual((chart["datasource"], chart["registryUrl"], chart["currentValue"]),
                         ("helm", CHART_REPO, VERSIONS["chart_versions"]["sealed_secrets"]))
        rules = config["packageRules"]
        index = next(i for i, rule in enumerate(rules) if rule.get("groupName") == "Sealed Secrets")
        rule = rules[index]
        self.assertIs(rule["automerge"], False)
        self.assertTrue(rule["description"])
        self.assertEqual(set(rule["matchPackageNames"]), {"sealed-secrets", IMAGE_REPO})
        automerge = [i for i, other in enumerate(rules) if other.get("automerge") is True]
        self.assertTrue(automerge and max(automerge) < index, "The never-automerge rule must come after every automerge rule")
        later = [other for other in rules[index + 1:] if "automerge" in other or "groupName" in other]
        for other in later:
            names = other.get("matchPackageNames", [])
            self.assertFalse({"sealed-secrets", IMAGE_REPO} & set(names), "A later rule overrides the Sealed Secrets rule")


if __name__ == "__main__":
    unittest.main()
