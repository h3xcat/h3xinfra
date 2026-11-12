"""Render the pinned controller chart without contacting Kubernetes or DNS."""

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import jinja2
import yaml


LAYER = Path(__file__).resolve().parents[1]
ROOT = LAYER.parents[1]


class ExternalDNSSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="h3x-externaldns-test-")
        cls.addClassCleanup(cls.work.cleanup)
        versions = yaml.safe_load((ROOT / "inventory/production/group_vars/all/versions.yml").read_text())
        version = versions["chart_versions"]["external_dns"]
        chart = os.environ.get("EXTERNALDNS_CHART_DIR")
        if not chart:
            env = dict(os.environ, HELM_REPOSITORY_CONFIG=f"{cls.work.name}/repositories.yaml",
                       HELM_REPOSITORY_CACHE=f"{cls.work.name}/cache")
            subprocess.run([
                "helm", "pull", "external-dns", "--repo", "https://kubernetes-sigs.github.io/external-dns/",
                "--version", version, "--untar", "--untardir", cls.work.name,
            ], env=env, check=True, capture_output=True, text=True, timeout=120)
            chart = f"{cls.work.name}/external-dns"
        if yaml.safe_load((Path(chart) / "Chart.yaml").read_text())["version"] != version:
            raise AssertionError("ExternalDNS test chart must match the inventory pin")
        cls.chart = chart
        plays = yaml.safe_load((LAYER / "standup.yml").read_text())
        cls.task = next(task["kubernetes.core.helm"] for play in plays for task in play.get("tasks", [])
                        if task.get("name") == "Deploy ExternalDNS via Helm")
        example = yaml.safe_load((ROOT / "inventory/production/host_vars/k8s/externaldns.yml").read_text())["externaldns"]
        cases = [example, {
            "namespace": "dns-test", "release_name_prefix": "test-dns",
            "domain_filters": ["example.invalid", "child.example.invalid"],
        }]
        cls.cases = [(config, cls.render(config), cls.render(config, previous_sources=True)) for config in cases]

    @classmethod
    def render(cls, config, previous_sources=False):
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["to_json"] = json.dumps

        def resolve(value):
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if isinstance(value, str):
                return env.from_string(value).render(externaldns=config)
            return value

        values = resolve(cls.task["values"])
        for item in resolve(cls.task["set_values"]):
            if item["value_type"] != "json":
                raise AssertionError("Expected the deployed JSON domain filter input")
            key, value = item["value"].split("=", 1)
            values[key] = json.loads(value)
        if previous_sources:
            values["sources"] = ["ingress", "service"]
        result = subprocess.run([
            "helm", "template", resolve(cls.task["name"]), cls.chart,
            "--namespace", resolve(cls.task["namespace"]), "-f", "-",
        ], input=yaml.safe_dump(values), check=True, capture_output=True, text=True, timeout=120)
        docs = [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]
        by_kind = {doc["kind"]: doc for doc in docs}
        if len(docs) != len(by_kind):
            raise AssertionError("Review the new resources instead of masking a duplicate kind")
        return by_kind

    def test_service_source_preserves_dns_scope_and_ownership(self):
        for config, docs, _ in self.cases:
            with self.subTest(config=config):
                container = docs["Deployment"]["spec"]["template"]["spec"]["containers"][0]
                args = container["args"]
                self.assertEqual([arg for arg in args if arg.startswith("--source=")], ["--source=service"])
                for flag in ("--policy=sync", "--registry=txt", "--provider=cloudflare",
                             "--label-filter=external-dns.alpha.kubernetes.io/publish=true"):
                    self.assertIn(flag, args)
                self.assertEqual([arg for arg in args if arg.startswith("--domain-filter=")],
                                 [f"--domain-filter={domain}" for domain in config["domain_filters"]])
                self.assertFalse(any(arg.startswith(("--txt-owner-id=", "--txt-prefix=", "--txt-suffix=")) for arg in args),
                                 "Changing the existing TXT identity requires a separate ownership migration")
                self.assertEqual(container["env"], [{
                    "name": "CF_API_TOKEN", "valueFrom": {"secretKeyRef": {
                        "name": f"{config['release_name_prefix']}-pre-cloudflare-api-token", "key": "api-token",
                    }},
                }])

    def test_only_ingress_argument_and_read_permission_change(self):
        for config, current, previous in self.cases:
            with self.subTest(config=config):
                self.assertEqual(set(current), {"ServiceAccount", "ClusterRole", "ClusterRoleBinding", "Service", "Deployment"})
                previous = copy.deepcopy(previous)
                args = previous["Deployment"]["spec"]["template"]["spec"]["containers"][0]["args"]
                args.remove("--source=ingress")
                previous["ClusterRole"]["rules"].remove({
                    "apiGroups": ["extensions", "networking.k8s.io"],
                    "resources": ["ingresses"], "verbs": ["get", "watch", "list"],
                })
                self.assertEqual(previous, current, "All other rendered fields must survive the source reduction")
                self.assertEqual(current["ClusterRole"]["rules"], [
                    {"apiGroups": [""], "resources": ["nodes"], "verbs": ["list", "watch"]},
                    {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "watch", "list"]},
                    {"apiGroups": [""], "resources": ["services"], "verbs": ["get", "watch", "list"]},
                    {"apiGroups": ["discovery.k8s.io"], "resources": ["endpointslices"], "verbs": ["get", "watch", "list"]},
                ])

    def test_controller_security_and_rollout_strategy(self):
        for config, docs, _ in self.cases:
            with self.subTest(config=config):
                deployment = docs["Deployment"]["spec"]
                self.assertEqual(deployment["replicas"], 1)
                self.assertEqual(deployment["strategy"], {"type": "Recreate"})
                pod = deployment["template"]["spec"]
                self.assertTrue(pod["securityContext"]["runAsNonRoot"])
                self.assertEqual(pod["securityContext"]["seccompProfile"], {"type": "RuntimeDefault"})
                security = pod["containers"][0]["securityContext"]
                self.assertFalse(security["allowPrivilegeEscalation"])
                self.assertFalse(security["privileged"])
                self.assertTrue(security["readOnlyRootFilesystem"])
                self.assertTrue(security["runAsNonRoot"])
                self.assertEqual(security["capabilities"], {"drop": ["ALL"]})


if __name__ == "__main__":
    unittest.main()
