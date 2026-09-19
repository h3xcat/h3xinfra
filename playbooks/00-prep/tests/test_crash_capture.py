"""Exercise storage safety checks without installing packages or touching hosts."""

import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from jinja2 import Template
import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("crash_capture_preflight", ROOT / "files/crash-capture-preflight.py")
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class StorageTests(unittest.TestCase):
    def test_normal_local_path(self):
        PREFLIGHT.validate_paths("/var/crash/kdump", "/var/crash")

    def test_root_filesystem_is_never_a_dump_target(self):
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_paths("/var/crash/kdump", "/")

    def test_rejects_unsafe_paths(self):
        for path in ("relative", "/", "/var/../crash", "/var//crash", "/var/crash/", "/mnt/a b", "/mnt/$(id)", "/var/lib/longhorn/crash", "/var/lib/kubelet/crash", "/var/lib/rancher/crash", "/run/crash"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                PREFLIGHT.validate_paths(path, "/var/crash")

    def test_rejects_missing_or_wrong_mount_relationship(self):
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_paths("/var/crash/kdump", "/mnt/crash")
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_mount(self.mount(), "/mnt/crash")

    def test_rejects_symlinked_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            (parent / "real").mkdir()
            (parent / "link").symlink_to(parent / "real", target_is_directory=True)
            with self.assertRaises(ValueError):
                PREFLIGHT.validate_paths(str(parent / "link" / "dumps"), str(parent))

    def mount(self, **changes):
        result = {"target": "/", "source": "/dev/mapper/ubuntu--vg-root", "fstype": "ext4", "options": "rw,relatime", "fsroot": "/"}
        result.update(changes)
        return result

    def test_accepts_local_ext4_and_xfs(self):
        for fstype in ("ext4", "xfs"):
            PREFLIGHT.validate_mount(self.mount(fstype=fstype), "/")

    def test_requires_matching_persistent_mount(self):
        mount = self.mount(target="/srv/crash")
        PREFLIGHT.validate_persistence(mount, [mount])
        for entries in ([], [mount, mount], [dict(mount, source="/dev/other")],
                        [dict(mount, target="/srv/wrong")], [dict(mount, fstype="xfs")]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                PREFLIGHT.validate_persistence(mount, entries)

    def test_rejects_optional_automounted_or_bind_fstab(self):
        mount = self.mount(target="/srv/crash")
        for option in ("noauto", "nofail", "ro", "bind", "rbind", "_netdev", "x-systemd.automount"):
            with self.subTest(option=option), self.assertRaises(ValueError):
                PREFLIGHT.validate_persistence(mount, [dict(mount, options=f"defaults,{option}")])

    def test_rejects_workload_filesystem_under_another_mount_alias(self):
        existing = {"/": 1, "/var/lib/longhorn": 2, "/var/lib/kubelet": 3, "/var/lib/rancher": 4}
        PREFLIGHT.validate_dedicated_filesystem(5, existing)
        for device in existing.values():
            with self.subTest(device=device), self.assertRaises(ValueError):
                PREFLIGHT.validate_dedicated_filesystem(device, existing)

    def test_rejects_nonlocal_bind_and_readonly_filesystems(self):
        for changes in (
            {"fstype": "nfs4"}, {"fstype": "cifs"}, {"fstype": "tmpfs"},
            {"fstype": "overlay"}, {"source": "/dev/longhorn/volume"},
            {"source": "/dev/mapper/ubuntu--vg-longhorn--storage--lv"},
            {"source": "/dev/sda1[/somewhere]"}, {"fsroot": "/somewhere"},
            {"source": "server:/crash"}, {"options": "ro,relatime"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PREFLIGHT.validate_mount(self.mount(**changes), "/")

    def disk(self, **changes):
        result = {"name": "/dev/nvme0n1", "type": "disk", "tran": "nvme"}
        result.update(changes)
        return result

    def test_accepts_local_lvm_ancestry(self):
        devices = [{"name": "/dev/mapper/vg-root", "type": "lvm", "children": [
            {"name": "/dev/nvme0n1p3", "type": "part", "children": [self.disk()]}
        ]}]
        PREFLIGHT.validate_devices(devices, {"/dev/nvme0n1": "pcie"})

    def test_rejects_remote_usb_and_unknown_disks(self):
        for transport in ("iscsi", "usb", "fc", "", None):
            with self.subTest(transport=transport), self.assertRaises(ValueError):
                PREFLIGHT.validate_devices([self.disk(tran=transport)], {})
        for transport in ("tcp", "rdma", None):
            with self.subTest(nvme=transport), self.assertRaises(ValueError):
                PREFLIGHT.validate_devices([self.disk()], {"/dev/nvme0n1": transport})

    def test_rejects_crypt_loop_and_unknown_ancestry(self):
        for kind in ("crypt", "loop", "mpath", "raid1"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                PREFLIGHT.validate_devices([self.disk(type=kind)], {})
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_devices([self.disk(type="part")], {})

    def test_rejects_one_nonlocal_disk_in_multi_pv_lvm(self):
        devices = [self.disk(), self.disk(name="/dev/sda", tran="iscsi")]
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_devices(devices, {"/dev/nvme0n1": "pcie"})

    def test_capacity_requires_next_dump_before_old_dump_is_pruned(self):
        gib = PREFLIGHT.GIB
        required = PREFLIGHT.validate_capacity(96 * gib, 200 * gib, 2 * gib, gib)
        self.assertEqual(required, 200 * gib)
        for free in (98 * gib, 104 * gib, 199 * gib):
            with self.subTest(free=free), self.assertRaises(ValueError):
                PREFLIGHT.validate_capacity(96 * gib, free, 2 * gib, gib)

    def test_checks_package_and_boot_workspace(self):
        gib = PREFLIGHT.GIB
        for root, boot in ((gib, gib), (2 * gib, gib // 2 - 1)):
            with self.subTest(root=root, boot=boot), self.assertRaises(ValueError):
                PREFLIGHT.validate_capacity(gib, 200 * gib, root, boot)


class PlaybookSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.play = yaml.safe_load((ROOT / "crash-capture.yml").read_text())[0]
        cls.tasks = yaml.safe_load((ROOT / "tasks/crash-capture.yml").read_text())

    def test_standalone_single_supported_worker(self):
        self.assertEqual(self.play["hosts"], "agent")
        guards = self.play["pre_tasks"][0]["ansible.builtin.assert"]["that"]
        self.assertIn("inventory_hostname not in groups.get('server', [])", guards)
        self.assertIn("ansible_facts.architecture == 'x86_64'", guards)
        self.assertIn("ansible_facts.distribution_version == '24.04'", guards)
        self.assertIn("ansible_play_hosts_all | length == 1", guards)
        self.assertNotIn("crash-capture", (ROOT / "standup.yml").read_text())

    def test_preflight_never_requires_maintenance_or_runs_mutations(self):
        for task in self.play["pre_tasks"]:
            self.assertEqual(task["tags"], "always")
            modules = set(task) - {"name", "tags", "register", "changed_when", "check_mode"}
            self.assertTrue(modules <= {"ansible.builtin.assert", "ansible.builtin.command", "ansible.builtin.debug", "ansible.builtin.slurp"})
        probe = self.play["pre_tasks"][1]
        self.assertFalse(probe["changed_when"])
        self.assertFalse(probe["check_mode"])
        self.assertEqual(self.play["tasks"][0]["tags"], "stage")

    def test_flags_are_required_before_any_mutation(self):
        guards = self.tasks[0]["ansible.builtin.assert"]["that"]
        self.assertIn("crash_capture_maintenance_confirmed | default(false) is sameas true", guards)
        self.assertIn("crash_capture_kured_inhibited | default(false) is sameas true", guards)
        self.assertIn("crash_capture_loaded.content | b64decode | trim == '0'", guards)

    def test_packages_cannot_start_services_and_needrestart_guard_comes_first(self):
        apt_index = next(i for i, task in enumerate(self.tasks) if "ansible.builtin.apt" in task)
        guard_index = next(i for i, task in enumerate(self.tasks) if task.get("ansible.builtin.import_tasks") == "needrestart.yml")
        self.assertLess(guard_index, apt_index)
        apt = self.tasks[apt_index]["ansible.builtin.apt"]
        self.assertEqual(apt["policy_rc_d"], 101)
        self.assertEqual(apt["state"], "present")
        self.assertNotIn("linux-crashdump", apt["name"])

    def test_no_restart_reboot_crash_or_load_commands(self):
        for task in self.tasks:
            self.assertNotIn("ansible.builtin.reboot", task)
            self.assertNotIn("ansible.builtin.shell", task)
            if "ansible.builtin.systemd_service" in task:
                service = task["ansible.builtin.systemd_service"]
                self.assertNotIn("state", service)
                self.assertEqual(service["name"], "kdump-tools.service")
            if "ansible.builtin.command" in task:
                self.assertIn(task["ansible.builtin.command"], (
                    "update-grub",
                    {"argv": ["/etc/kernel/postinst.d/kdump-tools", "{{ ansible_facts.kernel }}"]},
                ))

    def test_dump_permissions_and_retention(self):
        config = next(task for task in self.tasks if "ansible.builtin.lineinfile" in task)
        self.assertEqual(config["ansible.builtin.lineinfile"]["mode"], "0600")
        self.assertTrue(config["ansible.builtin.lineinfile"]["backup"])
        settings = {item["key"]: item["value"] for item in config["loop"]}
        self.assertEqual(settings["KDUMP_NUM_DUMPS"], "1")
        self.assertEqual(settings["KDUMP_DUMP_DMESG"], "1")
        self.assertEqual(settings["MAKEDUMP_ARGS"], '"-c -d 31"')
        for key in ("NFS", "SSH", "FTP"):
            self.assertEqual(settings[key], '""')
        self.assertEqual(settings["KDUMP_KERNEL"], "/var/lib/kdump/vmlinuz")
        self.assertEqual(settings["KDUMP_INITRD"], "/var/lib/kdump/initrd.img")
        self.assertEqual(settings["KDUMP_CMDLINE"], '""')
        self.assertIn("systemd.unit=kdump-tools-dump.service", settings["KDUMP_CMDLINE_APPEND"])
        dropin = next(task for task in self.tasks if task.get("loop") == ["kdump-tools", "kdump-tools-dump"])
        content = dropin["ansible.builtin.copy"]["content"]
        self.assertIn("RequiresMountsFor=", content)
        self.assertIn("AssertPathIsMountPoint=", content)
        self.assertIn("UMask=0077", content)

    def test_normalization_replaces_last_indented_or_exported_assignment(self):
        config = next(task for task in self.tasks if "ansible.builtin.lineinfile" in task)
        key = "KDUMP_CMDLINE_APPEND"
        regexp = config["ansible.builtin.lineinfile"]["regexp"].replace("{{ item.key }}", key)
        template = next(item["value"] for item in config["loop"] if item["key"] == key)
        value = Template(template).render()
        for variant in ("KDUMP_CMDLINE_APPEND=unsafe", "  KDUMP_CMDLINE_APPEND=unsafe", "export KDUMP_CMDLINE_APPEND=unsafe", "\texport\tKDUMP_CMDLINE_APPEND=unsafe"):
            with self.subTest(variant=variant):
                lines = [f"{key}=old", variant]
                matched = [i for i, line in enumerate(lines) if re.search(regexp, line)]
                self.assertEqual(matched, [0, 1])
                lines[matched[-1]] = f"{key}={value}"
                result = subprocess.run(
                    ["/bin/sh", "-c", "\n".join(lines) + '\nprintf "%s" "$KDUMP_CMDLINE_APPEND"'],
                    check=True, capture_output=True, text=True, timeout=5,
                )
                self.assertIn("systemd.unit=kdump-tools-dump.service", result.stdout)


if __name__ == "__main__":
    unittest.main()
