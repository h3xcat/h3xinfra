"""Validate capture-only native sysctl override ordering without changing sysctls."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "files/h3xinfra-kdump-sysctl"
OVERRIDES = "vm.nr_hugepages=0\nvm.nr_hugepages_mempolicy=0\n"


class CaptureSysctlTests(unittest.TestCase):
    def test_syntax_and_native_hook_dependency(self):
        subprocess.run(["/bin/sh", "-n", str(HOOK)], check=True, timeout=5)
        result = subprocess.run(["/bin/sh", str(HOOK), "prereqs"], check=True, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.stdout, "kdump-sysctl\n")
        self.assertIn("OPTION=KDUMP", HOOK.read_text())

    def exercise(self, capture, configs):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            root = temporary / "root"
            root.mkdir()
            runtime = temporary / "runtime"
            vmcore = temporary / "vmcore"
            source = temporary / "sysctl.conf"
            source.write_text(OVERRIDES)
            if capture:
                vmcore.touch()
            originals = {}
            for location, name, content in configs:
                parent = runtime if location == "runtime" else root / location
                parent.mkdir(parents=True, exist_ok=True)
                path = parent / name
                path.write_text(content)
                originals[path] = content
            fixture = HOOK.read_text().replace("[ -e /proc/vmcore ]", f'[ -e "{vmcore}" ]')
            fixture = fixture.replace("KDUMP_SYSCTL_PATH=/etc/kdump/sysctl.conf", f'KDUMP_SYSCTL_PATH="{source}"')
            fixture = fixture.replace("RUNTIME_SYSCTL_DIR=/run/sysctl.d", f'RUNTIME_SYSCTL_DIR="{runtime}"')
            hook = temporary / "hook"
            hook.write_text(fixture)
            result = subprocess.run(["/bin/sh", str(hook)], env=dict(os.environ, rootmnt=str(root)), capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            for path, content in originals.items():
                self.assertEqual(path.read_text(), content)
            generated = list(runtime.glob("*-h3xinfra-kdump.conf"))
            if not capture:
                self.assertFalse(generated)
                return
            self.assertEqual(len(generated), 1)
            self.assertEqual(generated[0].read_text(), OVERRIDES)
            if configs:
                self.assertGreater(generated[0].name, max(name for _, name, _ in configs))
            ordered = sorted([*originals, *generated], key=lambda path: path.name)
            settings = {}
            for path in ordered:
                for line in path.read_text().splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        key, value = line.split("=", 1)
                        settings[key.strip()] = value.strip()
            self.assertEqual(settings["vm.nr_hugepages"], "0")
            self.assertEqual(settings["vm.nr_hugepages_mempolicy"], "0")

    def test_normal_boot_has_no_side_effects(self):
        self.exercise(False, [("etc/sysctl.d", "99-longhorn-v2.conf", "vm.nr_hugepages=1024\n")])

    def test_overrides_longhorn_after_broken_native_output(self):
        self.exercise(True, [("etc/sysctl.d", "99-longhorn-v2.conf", "vm.nr_hugepages=1024\n"), ("runtime", "-kdump.conf", OVERRIDES)])

    def test_each_root_and_runtime_directory_can_contain_the_last_config(self):
        for location in ("usr/lib/sysctl.d", "usr/local/lib/sysctl.d", "lib/sysctl.d", "etc/sysctl.d", "run/sysctl.d", "runtime"):
            with self.subTest(location=location):
                self.exercise(True, [("etc/sysctl.d", "99-longhorn-v2.conf", "vm.nr_hugepages=1024\n"), (location, "zz-last.conf", "vm.nr_hugepages=2048\n")])

    def test_empty_configuration_still_installs_native_overrides(self):
        self.exercise(True, [])

    def test_installed_for_both_modes_before_capture_initramfs_rebuild(self):
        tasks = yaml.safe_load((ROOT / "tasks/crash-capture.yml").read_text())
        index, task = next((index, task) for index, task in enumerate(tasks) if task.get("ansible.builtin.copy", {}).get("src") == "h3xinfra-kdump-sysctl")
        self.assertNotIn("when", task)
        self.assertEqual(task["ansible.builtin.copy"]["dest"], "/etc/initramfs-tools/scripts/local-bottom/h3xinfra-kdump-sysctl")
        self.assertEqual(task["ansible.builtin.copy"]["mode"], "0755")
        rebuild = next(index for index, task in enumerate(tasks) if task.get("ansible.builtin.command", {}) == {"argv": ["/etc/kernel/postinst.d/kdump-tools", "{{ ansible_facts.kernel }}"]})
        self.assertLess(index, rebuild)


if __name__ == "__main__":
    unittest.main()
