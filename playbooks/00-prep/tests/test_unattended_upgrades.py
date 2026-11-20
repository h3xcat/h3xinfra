"""Check the unattended-upgrades tasks and apt files without touching hosts."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
TASKS = "tasks/unattended-upgrades.yml"
SCRIPT = ROOT / "files" / "h3xinfra-patching-metrics.sh"
TEXTFILE_DIR = "/var/lib/node_exporter/textfile_collector"


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tasks = load(TASKS)

    def test_needrestart_guard_comes_first(self):
        tasks = load("standup.yml")[0]["tasks"]
        self.assertEqual(tasks[0].get("ansible.builtin.import_tasks"), "tasks/needrestart.yml")
        self.assertIn(TASKS, [task.get("ansible.builtin.import_tasks") for task in tasks[1:]])

    def test_apt_files_are_validated_before_install(self):
        copies = [task["ansible.builtin.copy"] for task in self.tasks
                  if task.get("ansible.builtin.copy", {}).get("dest", "").startswith("/etc/apt/")]
        # apt loads files lexically: 52 keeps our reboot policy after the
        # distribution's 50unattended-upgrades defaults.
        self.assertEqual(
            sorted(copy["src"] for copy in copies),
            ["20auto-upgrades", "52h3xinfra-unattended-upgrades"],
        )
        for copy in copies:
            with self.subTest(dest=copy["dest"]):
                self.assertEqual(copy["dest"], f"/etc/apt/apt.conf.d/{copy['src']}")
                self.assertTrue((ROOT / "files" / copy["src"]).is_file())
                self.assertEqual(copy["validate"], "/usr/bin/apt-config -c %s dump")
                self.assertEqual(copy["mode"], "0644")

    def test_timers_are_enabled(self):
        timers = next(task for task in self.tasks if "ansible.builtin.systemd_service" in task)
        self.assertEqual(timers["loop"], ["apt-daily.timer", "apt-daily-upgrade.timer"])
        self.assertIs(timers["ansible.builtin.systemd_service"]["enabled"], True)

    def test_no_upgrade_reboot_or_restart_actions(self):
        modules = {"ansible.builtin.apt", "ansible.builtin.copy", "ansible.builtin.file",
                   "ansible.builtin.systemd_service"}
        for task in self.tasks:
            with self.subTest(task=task["name"]):
                used = modules.intersection(task)
                self.assertEqual(len(used), 1)
                args = task[used.pop()]
                self.assertNotIn("upgrade", args)
                self.assertNotIn("restarted", str(args.get("state")))
        apt = next(task["ansible.builtin.apt"] for task in self.tasks if "ansible.builtin.apt" in task)
        self.assertEqual(apt, {"name": "unattended-upgrades", "state": "present"})

    def test_kernel_hook_requests_a_reboot_and_never_fails(self):
        task = next(task for task in self.tasks
                    if task.get("ansible.builtin.copy", {}).get("dest", "").startswith("/etc/kernel/postinst.d/"))
        copy = task["ansible.builtin.copy"]
        # run-parts skips file names that contain a dot.
        self.assertEqual(copy["dest"], "/etc/kernel/postinst.d/zz-h3xinfra-reboot-required")
        self.assertEqual(copy["mode"], "0755")
        self.assertEqual(task["when"], 'ansible_architecture == "x86_64"')
        lines = copy["content"].splitlines()
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertIn("touch /run/reboot-required || true", lines)
        self.assertEqual(lines[-1], "exit 0")
        # The kernel postinst runs the hooks with --exit-on-error: the hook
        # must exit 0 even when it cannot create the file.
        with tempfile.TemporaryDirectory() as tmp:
            for flag, created in ((Path(tmp) / "reboot-required", True),
                                  (Path(tmp) / "missing" / "reboot-required", False)):
                with self.subTest(flag=str(flag)):
                    hook = Path(tmp) / "hook"
                    hook.write_text(copy["content"].replace("/run/reboot-required", str(flag)))
                    result = subprocess.run(["sh", str(hook), "6.8.0-1-generic"], capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(flag.exists(), created)

    def test_patch_state_timer_writes_to_the_textfile_collector(self):
        names = [task["name"] for task in self.tasks]
        directory = next(task["ansible.builtin.file"] for task in self.tasks if "ansible.builtin.file" in task)
        self.assertEqual(directory["path"], TEXTFILE_DIR)
        self.assertEqual(directory["state"], "directory")
        # The directory exists before the timer can start the script.
        self.assertLess(names.index("Create the node-exporter textfile directory"),
                        names.index("Enable the patch state timer"))
        copies = {task["ansible.builtin.copy"]["dest"]: task for task in self.tasks if "ansible.builtin.copy" in task}
        script = copies["/usr/local/sbin/h3xinfra-patching-metrics"]["ansible.builtin.copy"]
        self.assertEqual(script["src"], SCRIPT.name)
        self.assertEqual(script["mode"], "0755")
        service = copies["/etc/systemd/system/h3xinfra-patching-metrics.service"]
        self.assertIn("ExecStart=/usr/local/sbin/h3xinfra-patching-metrics\n", service["ansible.builtin.copy"]["content"])
        self.assertIn(f"ReadWritePaths={TEXTFILE_DIR}\n", service["ansible.builtin.copy"]["content"])
        timer = copies["/etc/systemd/system/h3xinfra-patching-metrics.timer"]
        self.assertIn("OnUnitActiveSec=15min\n", timer["ansible.builtin.copy"]["content"])
        enable = next(task for task in self.tasks if task["name"] == "Enable the patch state timer")
        self.assertEqual(enable["ansible.builtin.systemd_service"]["name"], "h3xinfra-patching-metrics.timer")
        self.assertIs(enable["ansible.builtin.systemd_service"]["enabled"], True)
        self.assertEqual(
            enable["ansible.builtin.systemd_service"]["daemon_reload"],
            "{{ %s.changed or %s.changed }}" % (service["register"], timer["register"]),
        )


class PatchStateScriptTests(unittest.TestCase):
    """Run the patch state script with a stub dpkg; never touches the host."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "bin").mkdir()
        self.metrics = self.tmp / "h3xinfra_patching.prom"
        self.stamp = self.tmp / "unattended-upgrades-stamp"

    def run_script(self, audit_output="", dpkg_status=0):
        dpkg = self.tmp / "bin" / "dpkg"
        dpkg.write_text('#!/bin/sh\n[ "$1" = --audit ] || exit 99\nprintf %s "$AUDIT_OUTPUT"\nexit "$DPKG_STATUS"\n')
        dpkg.chmod(0o755)
        env = dict(os.environ, PATH=f"{self.tmp / 'bin'}:{os.environ['PATH']}", STAMP_FILE=str(self.stamp),
                   METRICS_FILE=str(self.metrics), AUDIT_OUTPUT=audit_output, DPKG_STATUS=str(dpkg_status))
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)

    def samples(self):
        lines = self.metrics.read_text().splitlines()
        return dict(line.split() for line in lines if not line.startswith("#"))

    def test_healthy_node(self):
        self.stamp.touch()
        os.utime(self.stamp, (1795500000, 1795500000))
        self.assertEqual(self.run_script().returncode, 0)
        # The alert rules use these two names; do not rename them.
        self.assertEqual(self.samples(), {
            "h3xinfra_unattended_upgrades_stamp_timestamp_seconds": "1795500000",
            "h3xinfra_dpkg_audit_lines": "0",
        })
        self.assertEqual(oct(self.metrics.stat().st_mode & 0o777), "0o644")
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), sorted(["bin", self.metrics.name, self.stamp.name]))

    def test_interrupted_dpkg_without_stamp(self):
        audit = "The following packages are in a mess due to serious problems during\ninstallation.\n\n open-iscsi    iSCSI initiator tools\n"
        self.assertEqual(self.run_script(audit).returncode, 0)
        self.assertEqual(self.samples(), {
            "h3xinfra_unattended_upgrades_stamp_timestamp_seconds": "0",
            "h3xinfra_dpkg_audit_lines": "4",
        })

    def test_failing_dpkg_keeps_previous_file(self):
        self.assertEqual(self.run_script().returncode, 0)
        before = self.metrics.read_text()
        result = self.run_script("partial\n", dpkg_status=2)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.metrics.read_text(), before)
        self.assertEqual(sorted(path.name for path in self.tmp.iterdir()), ["bin", self.metrics.name])


@unittest.skipUnless(shutil.which("apt-config"), "apt-config is not installed")
class AptConfigTests(unittest.TestCase):
    def dump(self, name):
        result = subprocess.run(
            ["apt-config", "-c", str(ROOT / "files" / name), "dump"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.splitlines()

    def test_periodic_jobs_are_enabled(self):
        lines = self.dump("20auto-upgrades")
        self.assertIn('APT::Periodic::Update-Package-Lists "1";', lines)
        self.assertIn('APT::Periodic::Unattended-Upgrade "1";', lines)

    def test_unattended_upgrades_never_reboots(self):
        self.assertIn('Unattended-Upgrade::Automatic-Reboot "false";', self.dump("52h3xinfra-unattended-upgrades"))


if __name__ == "__main__":
    unittest.main()
