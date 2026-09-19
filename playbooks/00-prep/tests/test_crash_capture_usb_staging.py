"""Render USB staging boundaries and exercise the capture-only hook in isolation."""

import ast
import base64
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
UUID = "01234567-89ab-cdef-0123-456789abcdef"
MODULES = ["xhci_pci", "xhci_hcd", "ehci_pci", "ehci_hcd", "usb_storage", "uas", "sd_mod", "ext4"]


class UsbStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.play = yaml.safe_load((ROOT / "crash-capture.yml").read_text())[0]
        cls.tasks = yaml.safe_load((ROOT / "tasks/crash-capture.yml").read_text())
        cls.env = Environment(undefined=StrictUndefined)
        cls.env.filters["b64decode"] = lambda value: base64.b64decode(value).decode()
        cls.env.filters["regex_search"] = lambda value, pattern: re.search(pattern, value)
        cls.env.globals["lookup"] = lambda *args: "preflight source"

    def render(self, value, **variables):
        return self.env.from_string(value).render(**variables)

    def config_value(self, key):
        config = next(task for task in self.tasks if "ansible.builtin.lineinfile" in task)
        return next(item["value"] for item in config["loop"] if item["key"] == key)

    def test_default_preflight_retains_internal_only_storage(self):
        argv = ast.literal_eval(self.render(self.play["pre_tasks"][1]["ansible.builtin.command"]["argv"]))
        self.assertEqual(argv, ["/usr/bin/python3", "-c", "preflight source", "--directory", "/var/crash/kdump", "--mount", "/var/crash"])

    def test_usb_uuid_is_explicit_and_full_capacity_preflight_is_retained(self):
        argv = ast.literal_eval(self.render(self.play["pre_tasks"][1]["ansible.builtin.command"]["argv"], crash_capture_usb_uuid=UUID))
        self.assertEqual(argv[-2:], ["--usb-uuid", UUID])
        self.assertNotIn("--runtime-check", argv)

    def test_internal_capture_defaults_keep_usb_disabled(self):
        value = self.render(self.config_value("KDUMP_CMDLINE_APPEND"))
        self.assertEqual(value, '"reset_devices systemd.unit=kdump-tools-dump.service nr_cpus=1 irqpoll usbcore.nousb"')

    def test_usb_capture_enables_usb_without_losing_isolation_arguments(self):
        value = self.render(self.config_value("KDUMP_CMDLINE_APPEND"), crash_capture_usb_uuid=UUID)
        self.assertEqual(value, '"reset_devices systemd.unit=kdump-tools-dump.service nr_cpus=1 irqpoll"')
        self.assertEqual(self.config_value("KDUMP_CMDLINE"), '""')

    def test_usb_capture_assignment_is_valid_shell(self):
        for variables in ({}, {"crash_capture_usb_uuid": UUID}):
            with self.subTest(variables=variables):
                value = self.render(self.config_value("KDUMP_CMDLINE_APPEND"), **variables)
                result = subprocess.run(["/bin/sh", "-c", f'KDUMP_CMDLINE_APPEND={value}\nprintf "%s" "$KDUMP_CMDLINE_APPEND"'], capture_output=True, text=True, check=True, timeout=5)
                self.assertIn("systemd.unit=kdump-tools-dump.service", result.stdout)
                self.assertEqual("usbcore.nousb" in result.stdout, not variables)

    def test_inherited_usb_disable_and_blacklist_arguments_are_rejected_before_mutations(self):
        task = self.play["pre_tasks"][-1]
        self.assertEqual(task["tags"], "always")
        expression = self.env.compile_expression(task["ansible.builtin.assert"]["that"][0])
        for argument in ("usbcore.nousb", "usbcore.nousb=1", "nousb", "usbcore.authorized_default=0", "usbcore.authorized-default=2", "module_blacklist=xhci_pci", "module-blacklist=uas", "modprobe.blacklist=usb_storage", "rd.driver.blacklist=sd_mod", "rdblacklist=ext4", "initcall_blacklist=xhci_pci_init", '"usbcore.nousb=1"'):
            with self.subTest(argument=argument):
                variables = {"crash_capture_usb_uuid": UUID, "crash_capture_boot_cmdline": {"content": base64.b64encode(f"BOOT_IMAGE=/vmlinuz root=UUID=x {argument} quiet".encode()).decode()}}
                self.assertFalse(expression(**variables))
                variables.pop("crash_capture_usb_uuid")
                self.assertTrue(expression(**variables))
        safe = {"crash_capture_usb_uuid": UUID, "crash_capture_boot_cmdline": {"content": base64.b64encode(b"root=UUID=x ro quiet iommu=pt").decode()}}
        self.assertTrue(expression(**safe))

    def test_both_usb_services_pin_identity_without_capacity_checks(self):
        task = next(task for task in self.tasks if task.get("loop") == ["kdump-tools", "kdump-tools-dump"])
        template = task["ansible.builtin.copy"]["content"]
        rendered = self.render(template, crash_capture_usb_uuid=UUID, item="kdump-tools")
        self.assertIn("RequiresMountsFor=/var/crash/kdump", rendered)
        self.assertIn("AssertPathIsMountPoint=/var/crash", rendered)
        self.assertIn(f"ExecStartPre=/usr/bin/python3 /usr/local/libexec/h3xinfra-crash-capture-preflight --runtime-check --directory /var/crash/kdump --mount /var/crash --usb-uuid {UUID}", rendered)
        self.assertNotIn("ExecStartPre=", self.render(template, item="kdump-tools"))

    def test_capture_hugepages_are_logged_read_only_for_dump_service_in_both_modes(self):
        task = next(task for task in self.tasks if task.get("loop") == ["kdump-tools", "kdump-tools-dump"])
        template = task["ansible.builtin.copy"]["content"]
        command = "ExecStartPre=/usr/sbin/sysctl vm.nr_hugepages vm.nr_hugepages_mempolicy"
        for variables in ({}, {"crash_capture_usb_uuid": UUID}):
            with self.subTest(variables=variables):
                self.assertIn(command, self.render(template, item="kdump-tools-dump", **variables))
                self.assertNotIn(command, self.render(template, item="kdump-tools", **variables))
        self.assertNotIn(" -w", command)
        self.assertNotIn("=", command.split("sysctl", 1)[1])

    def test_internal_mode_removes_usb_hook_and_runtime_guard(self):
        cleanup = next(task for task in self.tasks if task.get("ansible.builtin.file", {}).get("state") == "absent")
        self.assertEqual(cleanup["loop"], ["/etc/initramfs-tools/hooks/h3xinfra-kdump-usb", "/usr/local/libexec/h3xinfra-crash-capture-preflight"])
        expression = self.env.compile_expression(cleanup["when"])
        self.assertTrue(expression())
        self.assertFalse(expression(crash_capture_usb_uuid=UUID))

    def test_package_hook_rebuilds_current_capture_only_image_without_loading_it(self):
        commands = [task["ansible.builtin.command"] for task in self.tasks if "ansible.builtin.command" in task]
        self.assertEqual(commands, ["update-grub", {"argv": ["/etc/kernel/postinst.d/kdump-tools", "{{ ansible_facts.kernel }}"]}])
        tasks = (ROOT / "tasks/crash-capture.yml").read_text()
        self.assertIn("kdump-tools: Generating /var/lib/kdump/initrd.img-", tasks)
        self.assertIn("crash_capture_initrd.stat.isreg", tasks)
        self.assertIn("crash_capture_initrd.stat.size", tasks)
        self.assertNotIn("update-initramfs", tasks)
        self.assertNotIn("/boot/initrd", tasks)


class UsbHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = ROOT / "files/h3xinfra-kdump-usb"

    def test_shell_syntax_and_prerequisite_query(self):
        subprocess.run(["/bin/sh", "-n", str(self.hook)], check=True, timeout=5)
        result = subprocess.run(["/bin/sh", str(self.hook), "prereqs"], capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(result.stdout, "")

    def test_normal_initramfs_never_reaches_module_helpers(self):
        for confdir, kdump in (("/etc/initramfs-tools", "y"), ("/etc/initramfs-tools", ""), ("/var/lib/kdump/initramfs-tools", ""), ("/var/lib/kdump/initramfs-tools-other", "y")):
            with self.subTest(confdir=confdir, kdump=kdump):
                result = subprocess.run(["/bin/sh", str(self.hook)], env={"CONFDIR": confdir, "KDUMP": kdump}, capture_output=True, text=True, check=True, timeout=5)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def run_capture_hook(self, missing_module=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = root / "hook-functions"
            helper.write_text('modinfo() { [ "$3" != "$MISSING_MODULE" ]; }\nforce_load() { printf "%s\\n" "$1" >> "$MODULE_LOG"; }\n')
            hook = root / "hook"
            hook.write_text(self.hook.read_text().replace(". /usr/share/initramfs-tools/hook-functions", f'. "{helper}"'))
            environment = dict(os.environ, CONFDIR="/var/lib/kdump/initramfs-tools", KDUMP="y", version="test-kernel", MODULE_LOG=str(root / "modules"), MISSING_MODULE=missing_module)
            result = subprocess.run(["/bin/sh", str(hook)], env=environment, capture_output=True, text=True, timeout=5)
            modules = (root / "modules").read_text().splitlines() if (root / "modules").exists() else []
            return result, modules

    def test_capture_hook_forces_storage_and_controller_modules(self):
        result, modules = self.run_capture_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(modules, MODULES)

    def test_missing_required_module_fails_build(self):
        result, modules = self.run_capture_hook("uas")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("uas", modules)
        self.assertNotIn("ext4", modules)


if __name__ == "__main__":
    unittest.main()
