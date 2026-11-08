"""Check the unattended-upgrades tasks and apt files without touching hosts."""

from pathlib import Path
import shutil
import subprocess
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
TASKS = "tasks/unattended-upgrades.yml"
GUARD = "tasks/needrestart.yml"


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def import_index(tasks, target):
    return next(i for i, task in enumerate(tasks) if task.get("ansible.builtin.import_tasks") == target)


class OrderTests(unittest.TestCase):
    def test_needrestart_guard_comes_first_in_both_playbooks(self):
        for playbook in ("standup.yml", "unattended-upgrades.yml"):
            with self.subTest(playbook=playbook):
                tasks = load(playbook)[0]["tasks"]
                self.assertEqual(import_index(tasks, GUARD), 0)
                self.assertGreater(import_index(tasks, TASKS), 0)

    def test_dedicated_playbook_targets_cluster_nodes(self):
        play = load("unattended-upgrades.yml")[0]
        self.assertEqual(play["hosts"], "k3s_cluster")
        self.assertIs(play["become"], True)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tasks = load(TASKS)

    def test_apt_files_are_validated_before_install(self):
        copies = [task["ansible.builtin.copy"] for task in self.tasks if "ansible.builtin.copy" in task]
        self.assertEqual(
            sorted(copy["dest"] for copy in copies),
            ["/etc/apt/apt.conf.d/20auto-upgrades", "/etc/apt/apt.conf.d/52h3xinfra-unattended-upgrades"],
        )
        for copy in copies:
            with self.subTest(dest=copy["dest"]):
                self.assertTrue((ROOT / "files" / copy["src"]).is_file())
                self.assertEqual(copy["validate"], "/usr/bin/apt-config -c %s dump")
                self.assertEqual(copy["mode"], "0644")

    def test_drop_in_loads_after_distribution_defaults(self):
        # apt reads apt.conf.d in lexical order and the last value wins, so the
        # Automatic-Reboot drop-in must sort after the packaged defaults.
        reboot = next(
            task["ansible.builtin.copy"]
            for task in self.tasks
            if "ansible.builtin.copy" in task and task["ansible.builtin.copy"]["src"] != "20auto-upgrades"
        )
        self.assertEqual(Path(reboot["dest"]).parent, Path("/etc/apt/apt.conf.d"))
        self.assertGreater(Path(reboot["dest"]).name, "50unattended-upgrades")

    def test_timers_are_enabled(self):
        timers = next(task for task in self.tasks if "ansible.builtin.systemd_service" in task)
        self.assertEqual(timers["loop"], ["apt-daily.timer", "apt-daily-upgrade.timer"])
        self.assertIs(timers["ansible.builtin.systemd_service"]["enabled"], True)

    def test_no_upgrade_reboot_or_restart_actions(self):
        modules = {"ansible.builtin.apt", "ansible.builtin.copy", "ansible.builtin.systemd_service"}
        for task in self.tasks:
            with self.subTest(task=task["name"]):
                used = modules.intersection(task)
                self.assertEqual(len(used), 1)
                args = task[used.pop()]
                self.assertNotIn("upgrade", args)
                self.assertNotIn("restarted", str(args.get("state")))
        apt = next(task["ansible.builtin.apt"] for task in self.tasks if "ansible.builtin.apt" in task)
        self.assertEqual(apt, {"name": "unattended-upgrades", "state": "present"})


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
