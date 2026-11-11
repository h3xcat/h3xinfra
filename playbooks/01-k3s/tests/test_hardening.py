"""Static checks for the k3s hardening options (audit policy, kubelet drop-ins)."""

from pathlib import Path
import unittest

import jinja2
import yaml


LAYER = Path(__file__).resolve().parents[1]
PREP = LAYER.parent / "00-prep" / "standup.yml"
POLICY = LAYER / "files" / "apiserver-audit-policy.yaml"

# Values the kubelet requires with protectKernelDefaults
# (kubernetes pkg/kubelet/cm/container_manager_linux.go, setupKernelTunables).
KUBELET_KERNEL_DEFAULTS = {
    "vm.overcommit_memory": "1",
    "vm.panic_on_oom": "0",
    "kernel.panic": "10",
    "kernel.panic_on_oops": "1",
    "kernel.keys.root_maxkeys": "1000000",
    "kernel.keys.root_maxbytes": "25000000",
}


def load(path):
    return yaml.safe_load(path.read_text())


def tasks(playbook):
    for play in playbook:
        yield from play.get("tasks", [])
        for task in play.get("tasks", []):
            yield from task.get("block", [])


def first_level(policy, group, resource, verb, user="admin", groups=("system:authenticated",)):
    """Return the level of the first audit rule that matches the request."""
    for rule in policy["rules"]:
        if "nonResourceURLs" in rule:
            continue
        if "verbs" in rule and verb not in rule["verbs"]:
            continue
        if "users" in rule and user not in rule["users"]:
            continue
        if "userGroups" in rule and not set(groups) & set(rule["userGroups"]):
            continue
        if "resources" in rule:
            matched = False
            for entry in rule["resources"]:
                if entry.get("group", "") != group:
                    continue
                names = entry.get("resources")
                if names is None or resource in names:
                    matched = True
            if not matched:
                continue
        return rule["level"]
    return "None"


class AuditPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = load(POLICY)

    def test_kind_and_stages(self):
        self.assertEqual(self.policy["apiVersion"], "audit.k8s.io/v1")
        self.assertEqual(self.policy["kind"], "Policy")
        self.assertIn("RequestReceived", self.policy["omitStages"])
        self.assertEqual(self.policy["rules"][-1], {"level": "Metadata"})

    def test_credentials_never_log_bodies(self):
        for group, resource in [("", "secrets"), ("", "configmaps"), ("", "serviceaccounts/token"),
                                ("authentication.k8s.io", "tokenreviews"),
                                ("certificates.k8s.io", "certificatesigningrequests")]:
            for verb in ["get", "list", "watch", "create", "update", "patch", "delete"]:
                with self.subTest(resource=resource, verb=verb):
                    self.assertEqual(first_level(self.policy, group, resource, verb), "Metadata")

    def test_rbac_and_workload_writes_record_bodies(self):
        self.assertEqual(first_level(self.policy, "rbac.authorization.k8s.io", "clusterrolebindings", "create"),
                         "RequestResponse")
        self.assertEqual(first_level(self.policy, "", "pods", "create"), "Request")
        self.assertEqual(first_level(self.policy, "apps", "daemonsets", "patch"), "Request")
        self.assertEqual(first_level(self.policy, "", "pods/exec", "create"), "Metadata")
        self.assertEqual(first_level(self.policy, "", "pods/status", "patch"), "Metadata")

    def test_noise_dropped(self):
        self.assertEqual(first_level(self.policy, "coordination.k8s.io", "leases", "update"), "None")
        self.assertEqual(first_level(self.policy, "coordination.k8s.io", "leases", "delete"), "Metadata")
        self.assertEqual(first_level(self.policy, "", "events", "create"), "None")

    def test_system_reads_dropped_but_credentials_kept(self):
        node = {"user": "system:node:n1", "groups": ("system:nodes", "system:authenticated")}
        kcm = {"user": "system:kube-controller-manager"}
        self.assertEqual(first_level(self.policy, "", "pods", "watch", **node), "None")
        self.assertEqual(first_level(self.policy, "", "nodes", "get", **node), "None")
        self.assertEqual(first_level(self.policy, "apps", "deployments", "list", **kcm), "None")
        # Credential reads and all writes by these identities stay recorded.
        self.assertEqual(first_level(self.policy, "", "secrets", "get", **node), "Metadata")
        self.assertEqual(first_level(self.policy, "", "configmaps", "list", **kcm), "Metadata")
        self.assertEqual(first_level(self.policy, "", "pods", "create", **kcm), "Request")
        self.assertEqual(first_level(self.policy, "", "nodes/status", "patch", **node), "Metadata")
        # Reads by other users stay at Metadata.
        self.assertEqual(first_level(self.policy, "", "pods", "list"), "Metadata")


class AuditPlaybookTests(unittest.TestCase):
    def test_serial_rollout_and_policy_path(self):
        playbook = load(LAYER / "apiserver-audit.yml")
        self.assertEqual(playbook[0]["serial"], 1)
        steps = list(tasks(playbook))
        policy_task = next(t for t in steps if t.get("ansible.builtin.copy", {}).get("src") == "files/apiserver-audit-policy.yaml")
        dropin_task = next(t for t in steps if "kube-apiserver-arg+" in t.get("ansible.builtin.copy", {}).get("content", ""))
        dest = policy_task["ansible.builtin.copy"]["dest"]
        self.assertIn(f'"audit-policy-file={dest}"', dropin_task["ansible.builtin.copy"]["content"])
        self.assertTrue(any("readyz" in str(t.get("ansible.builtin.command", "")) for t in steps))


class SecretsEncryptionPreconditionTests(unittest.TestCase):
    """The precondition must fail closed: pass only on a new cluster or a confirmed configuration."""

    def setUp(self):
        play = next(p for p in load(LAYER / "standup.yml") if p.get("name") == "Check the secrets encryption precondition")
        self.play = play
        assert_task = next(t for t in play["tasks"] if "ansible.builtin.assert" in t)
        that = assert_task["ansible.builtin.assert"]["that"]
        self.conditions = [that] if isinstance(that, str) else that
        self.env = jinja2.Environment()

    def passes(self, binary_exists, status=None, config_exists=False, annotation=None):
        context = {
            "k3s_secrets_encrypt_binary": {"stat": {"exists": binary_exists}},
            "k3s_secrets_encrypt_config": {"stat": {"exists": config_exists}},
            "k3s_secrets_encrypt_status": status if status is not None else {"skipped": True},
            "k3s_secrets_encrypt_annotation": annotation if annotation is not None else {"skipped": True},
        }
        return all(bool(self.env.compile_expression(c)(**context)) for c in self.conditions)

    def test_new_cluster_passes(self):
        self.assertTrue(self.passes(False))

    def test_enabled_configuration_passes(self):
        for stdout in ["Encryption Status: Disabled\nCurrent Rotation Stage: start\nServer Encryption Hashes: All hashes match",
                       "Encryption Status: Enabled\nCurrent Rotation Stage: reencrypt_finished"]:
            with self.subTest(stdout=stdout):
                self.assertTrue(self.passes(True, {"rc": 0, "stdout": stdout, "stderr": ""}))

    def test_missing_configuration_fails(self):
        self.assertFalse(self.passes(True, {"rc": 0, "stdout": "Encryption Status: Disabled, no configuration file found", "stderr": ""}))

    def test_status_error_fails(self):
        for status in [
            {"rc": 1, "stdout": "", "stderr": "level=fatal msg=\"dial tcp 127.0.0.1:6443: connect: connection refused\""},
            {"rc": 1, "stdout": "", "stderr": "failed to read token"},
            {"rc": 0, "stdout": "unexpected output", "stderr": ""},
            {"skipped": True},
        ]:
            with self.subTest(status=status):
                self.assertFalse(self.passes(True, status))

    STATUS_ERROR = {"rc": 1, "stdout": "", "stderr": "level=fatal msg=\"Error: see server log for details: "
                    "Internal error occurred: secret-encrypt error ID 27199\""}

    def test_enabled_before_restart_passes(self):
        # After `k3s secrets-encrypt enable`, status fails until the servers
        # restart with secrets-encryption (the restart this play allows).
        self.assertTrue(self.passes(True, self.STATUS_ERROR, config_exists=True,
                                    annotation={"rc": 0, "stdout": ""}))

    def test_status_error_without_configuration_fails(self):
        self.assertFalse(self.passes(True, self.STATUS_ERROR, config_exists=False,
                                     annotation={"rc": 0, "stdout": ""}))

    def test_status_error_with_annotation_or_unreadable_node_fails(self):
        for annotation in [{"rc": 0, "stdout": "start-abc123"}, {"rc": 1, "stdout": ""}, {"skipped": True}]:
            with self.subTest(annotation=annotation):
                self.assertFalse(self.passes(True, self.STATUS_ERROR, config_exists=True, annotation=annotation))

    def test_status_runs_only_when_binary_exists(self):
        status_task = next(t for t in self.play["tasks"] if t.get("register") == "k3s_secrets_encrypt_status")
        self.assertIn("k3s_secrets_encrypt_binary.stat.exists", status_task["when"])


class KubeletHardeningTests(unittest.TestCase):
    def setUp(self):
        self.standup = load(LAYER / "standup.yml")
        self.kubelet_play = next(p for p in self.standup if p.get("name") == "Create kubelet configuration")

    def test_pinned_values_match_kubelet(self):
        pinned = {item["name"]: item["value"] for item in self.kubelet_play["vars"]["kubelet_kernel_defaults"]}
        self.assertEqual(pinned, KUBELET_KERNEL_DEFAULTS)

    def test_prep_sysctls_do_not_conflict(self):
        """00-prep must not set a different value for a tunable the kubelet protects."""
        prep_task = next(t for t in tasks(load(PREP)) if t.get("name") == "Configure sysctl settings")
        for item in prep_task["loop"]:
            if item["name"] in KUBELET_KERNEL_DEFAULTS:
                with self.subTest(name=item["name"]):
                    self.assertEqual(item["value"], KUBELET_KERNEL_DEFAULTS[item["name"]])

    def test_kubelet_dropins_use_conf_extension(self):
        # kubelet --config-dir reads only *.conf files. Imported task files
        # (the unsafe sysctl allowlist) count too.
        steps = []
        for task in self.kubelet_play["tasks"]:
            imported = task.get("ansible.builtin.import_tasks")
            steps.extend(load(LAYER / imported) if imported else [task])
        self.assertTrue(any("99-unsafe-sysctls" in str(t) for t in steps))
        for task in steps:
            dest = task.get("ansible.builtin.copy", {}).get("dest", "")
            if "kubelet.conf.d" in dest:
                with self.subTest(dest=dest):
                    self.assertTrue(dest.endswith(".conf"))

    def test_hardening_dropin_writes_only_enabled_options(self):
        task = next(t for t in self.kubelet_play["tasks"] if t.get("name") == "Create kubelet configuration file for hardening options")
        content = task["ansible.builtin.copy"]["content"]
        self.assertNotIn(": false", content)
        self.assertIn("seccompDefault: true", content)
        self.assertIn("protectKernelDefaults: true", content)


if __name__ == "__main__":
    unittest.main()
