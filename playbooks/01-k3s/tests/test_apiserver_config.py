"""Exercise the combined play's control flow with synthetic module results.

No playbook or module is executed. Conditions, vars, loops, readiness and
failure expressions come from the real task definitions; module change/failure
results are fixtures. Generated-file equivalence is checked separately.
"""
import copy
import itertools
import unittest

from playbook_helpers import LAYER, conditions, environment, load, render_vars, when_true


PLAY = load(LAYER / "apiserver-config.yml")[0]
CHANGES = (
    "oidc_auth_file", "oidc_dropin", "pod_security_file", "pod_security_dropin",
    "pod_security_removed", "audit_policy_file", "audit_dropin", "audit_dropin_removed",
)


def steps(tasks, inherited=()):
    for task in tasks:
        when = [*inherited, *conditions(task)]
        if "block" in task:
            yield from steps(task["block"], when)
        else:
            task = copy.deepcopy(task)
            task["when"] = when
            yield task


TASKS = list(steps(PLAY["tasks"]))


class FailedStep(Exception):
    def __init__(self, events):
        self.events = events


def simulate(changed=(), enabled=(True, True, True), token="viewer-fixture",
             check=False, fail_at=None, levels=None):
    """Walk real tasks, substituting safe module results and recording actions."""
    context = {
        "ansible_check_mode": check,
        "apiserver_oidc": {"enabled": enabled[0], "smoke_test_token": token},
        "apiserver_pod_security": {"enabled": enabled[1], "defaults": levels or {}},
        "apiserver_audit": {"enabled": enabled[2]},
    }
    events = []
    for task in TASKS:
        variables = render_vars(PLAY["vars"], context)
        if not when_true(task, variables):
            if task.get("register"):
                context[task["register"]] = {"skipped": True, "changed": False}
            continue
        variables = render_vars(task.get("vars", {}), variables)
        if "ansible.builtin.assert" in task:
            if not all(environment().compile_expression(c)(**variables)
                       for c in task["ansible.builtin.assert"]["that"]):
                raise FailedStep(events)
            continue
        module = next(k for k in task if k.startswith("ansible.builtin."))
        task_failed = False
        for item in task.get("loop", [None]):
            local = {**variables, "item": item}
            marker = task.get("register", "")
            if module == "ansible.builtin.command":
                if marker == "apiserver_readyz":
                    event = "readyz"
                    result = {"rc": 1 if fail_at == event else 0,
                              "stdout": "failed" if fail_at == event else "ok"}
                else:
                    event = "smoke_" + item["verb"]
                    result = {"rc": 0 if item["verb"] == "get" else 1,
                              "stdout": "wrong" if fail_at == event else item["expect"]}
                events.append(event)
                local[marker] = result
                expression = task.get("until")
                if expression and not environment().compile_expression(expression)(**local):
                    raise FailedStep(events)
                expression = task.get("failed_when")
                if expression and environment().compile_expression(expression)(**local):
                    # Ansible completes all loop items before failing the task.
                    task_failed = True
            elif module == "ansible.builtin.systemd":
                events.append("predict_restart" if check else "restart")
                if fail_at == "restart":
                    raise FailedStep(events)
            else:
                if fail_at == marker:
                    raise FailedStep(events)
                args = task[module]
                path = args.get("dest", args.get("path"))
                path = environment().from_string(path).render(local)
                events.append((args.get("state", "write"), path))
        if task_failed:
            raise FailedStep(events)
        if task.get("register"):
            context[task["register"]] = {"changed": task["register"] in changed}
    return events


class ApiserverConfigurationTests(unittest.TestCase):
    def test_one_serial_restart_with_local_readiness_and_no_error_bypass(self):
        self.assertEqual(PLAY["hosts"], "server")
        self.assertEqual(PLAY["serial"], 1)
        self.assertIs(PLAY["become"], True)
        self.assertEqual(sum("ansible.builtin.systemd" in t for t in TASKS), 1)
        ready = next(t for t in TASKS if t.get("register") == "apiserver_readyz")
        self.assertEqual(ready["ansible.builtin.command"], "k3s kubectl get --raw=/readyz")
        self.assertEqual((ready["retries"], ready["delay"]), (40, 5))
        for task in TASKS:
            self.assertFalse(task.get("ignore_errors", False))
            self.assertNotIn("ansible.builtin.import_tasks", task)
        self.assertNotIn("rescue", str(PLAY))

    def test_each_file_change_restarts_once_and_unchanged_files_do_not(self):
        for marker in CHANGES:
            enabled = (True, marker != "pod_security_removed", marker != "audit_dropin_removed")
            with self.subTest(marker=marker):
                events = simulate(changed={marker}, enabled=enabled)
                self.assertEqual(events.count("restart"), 1)
                self.assertEqual(events.count("readyz"), 1)
                self.assertLess(events.index("restart"), events.index("readyz"))
        for changed in ((), {"directory-only"}):
            events = simulate(changed=changed)
            self.assertNotIn("restart", events)
            self.assertNotIn("readyz", events)
        self.assertEqual(simulate(changed=CHANGES).count("restart"), 1)

    def test_enable_disable_combinations_preserve_file_ownership(self):
        oidc = {"/etc/rancher/k3s/oidc-auth.yaml", "/etc/rancher/k3s/config.yaml.d/10-oidc-auth.yaml"}
        psa = ["/etc/rancher/k3s/config.yaml.d/20-pod-security.yaml", "/etc/rancher/k3s/pod-security-admission.yaml"]
        audit = "/etc/rancher/k3s/config.yaml.d/20-apiserver-audit.yaml"
        for enabled in itertools.product((False, True), repeat=3):
            with self.subTest(enabled=enabled):
                events = simulate(enabled=enabled)
                files = [event for event in events if isinstance(event, tuple)]
                writes = {path for action, path in files if action == "write"}
                removed = [path for action, path in files if action == "absent"]
                expected = (oidc if enabled[0] else set()) | (set(psa) if enabled[1] else set())
                if enabled[2]:
                    expected |= {"/etc/rancher/k3s/audit-policy.yaml", audit}
                self.assertEqual(writes, expected)
                self.assertEqual(removed, ([] if enabled[1] else psa) + ([] if enabled[2] else [audit]))
                self.assertFalse(oidc & set(removed))
                self.assertNotIn("/etc/rancher/k3s/audit-policy.yaml", removed)
                self.assertNotIn("/var/lib/rancher/k3s/server/logs", removed)

    def test_invalid_psa_levels_fail_before_any_file_operation(self):
        for key in ("enforce", "audit", "warn"):
            with self.subTest(level=key), self.assertRaises(FailedStep) as caught:
                simulate(levels={key: "unknown"})
            self.assertEqual(caught.exception.events, [])

    def test_smoke_remains_opt_in_oidc_only_and_after_readiness(self):
        smoke = next(t for t in TASKS if t.get("register") == "oidc_smoke")
        self.assertIs(smoke["no_log"], True)
        self.assertIs(smoke["run_once"], True)
        self.assertEqual(smoke["loop"], [{"verb": "get", "expect": "yes"}, {"verb": "create", "expect": "no"}])
        events = simulate(changed={"oidc_auth_file"})
        self.assertEqual(events[-3:], ["readyz", "smoke_get", "smoke_create"])
        for kwargs in ({"token": ""}, {"enabled": (False, True, True)}, {"changed": {"audit_dropin"}}):
            values = {"changed": {"oidc_auth_file"}, **kwargs}
            self.assertNotIn("smoke_get", simulate(**values))

    def test_check_mode_predicts_restart_without_readiness_or_token_commands(self):
        events = simulate(changed=CHANGES, check=True)
        self.assertIn("predict_restart", events)
        self.assertNotIn("restart", events)
        self.assertFalse(set(("readyz", "smoke_get", "smoke_create")) & set(e for e in events if isinstance(e, str)))

    def test_failures_stop_before_later_runtime_steps(self):
        for marker in CHANGES:
            enabled = (True, marker != "pod_security_removed", marker != "audit_dropin_removed")
            with self.subTest(stage=marker), self.assertRaises(FailedStep) as caught:
                simulate(changed=CHANGES, enabled=enabled, fail_at=marker)
            self.assertNotIn("restart", caught.exception.events)
        for failure, forbidden in (("restart", "readyz"), ("readyz", "smoke_get")):
            with self.subTest(stage=failure), self.assertRaises(FailedStep) as caught:
                simulate(changed=CHANGES, fail_at=failure)
            self.assertNotIn(forbidden, caught.exception.events)
        for failure in ("smoke_get", "smoke_create"):
            with self.subTest(stage=failure), self.assertRaises(FailedStep) as caught:
                simulate(changed=CHANGES, fail_at=failure)
            self.assertEqual(caught.exception.events[-2:], ["smoke_get", "smoke_create"])


if __name__ == "__main__":
    unittest.main()
