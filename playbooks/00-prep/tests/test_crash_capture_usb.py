"""Exercise explicit USB capture identity checks without accessing block devices."""

from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("crash_capture_usb_preflight", ROOT / "files/crash-capture-preflight.py")
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)
UUID = "12abcdef-3456-7890-abcd-123456789abc"
OPTIONS = "defaults,nofail,x-systemd.device-timeout=30s"


def usb_disk(**changes):
    disk = {"name": "/dev/sdz", "type": "disk", "tran": "usb"}
    disk.update(changes)
    return disk


def usb_partition(**changes):
    partition = {"name": "/dev/sdz1", "type": "part", "children": [usb_disk()]}
    partition.update(changes)
    return partition


class USBValidationTests(unittest.TestCase):
    def setUp(self):
        self.mount = {
            "target": "/srv/crash", "source": "/dev/sdz1", "fstype": "ext4",
            "options": "rw,relatime", "fsroot": "/", "uuid": UUID,
        }
        self.evaluated = dict(self.mount, options=OPTIONS)
        self.raw = dict(self.evaluated, source=f"UUID={UUID}")

    def test_only_canonical_uuid_can_enable_usb(self):
        PREFLIGHT.validate_usb_uuid(None)
        PREFLIGHT.validate_usb_uuid(UUID)
        for value in ("", UUID.upper(), "UUID=" + UUID, UUID + "\n", "../../dev/sda", "$(id)", "--help", "1234"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PREFLIGHT.validate_usb_uuid(value)

    def test_usb_requires_the_expected_mounted_ext4_uuid(self):
        PREFLIGHT.validate_mount(self.mount, "/srv/crash", UUID)
        for changes in (
            {"fstype": "xfs"}, {"fstype": "vfat"}, {"uuid": None},
            {"uuid": "ffffffff-ffff-ffff-ffff-ffffffffffff"},
            {"target": "/"}, {"fsroot": "/subdir"}, {"options": "ro"},
            {"source": "/dev/longhorn/volume"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PREFLIGHT.validate_mount(dict(self.mount, **changes), "/srv/crash", UUID)

    def test_usb_requires_both_raw_uuid_and_evaluated_device(self):
        PREFLIGHT.validate_persistence(self.mount, [self.evaluated], UUID, [self.raw])
        for source in ("/dev/sdz1", "LABEL=kdump", "UUID=ffffffff-ffff-ffff-ffff-ffffffffffff"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                PREFLIGHT.validate_persistence(self.mount, [self.evaluated], UUID, [dict(self.raw, source=source)])
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_persistence(self.mount, [dict(self.evaluated, source="/dev/other")], UUID, [self.raw])

    def test_usb_requires_one_matching_raw_and_evaluated_entry(self):
        for entries in (None, [], [self.raw, self.raw], [dict(self.raw, target="/elsewhere")],
                        [dict(self.raw, fstype="xfs")], [dict(self.raw, options="defaults")]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                PREFLIGHT.validate_persistence(self.mount, [self.evaluated], UUID, entries)
        for entries in ([], [self.evaluated, self.evaluated]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                PREFLIGHT.validate_persistence(self.mount, entries, UUID, [self.raw])

    def test_usb_requires_optional_boot_mount_with_bounded_device_wait(self):
        for options in ("defaults", "defaults,nofail", "defaults,x-systemd.device-timeout=30s",
                        "defaults,nofail,x-systemd.device-timeout=0", OPTIONS + ",x-systemd.device-timeout=0",
                        OPTIONS + ",x-systemd.device-timeout=30s"):
            with self.subTest(options=options), self.assertRaises(ValueError):
                PREFLIGHT.validate_persistence(
                    self.mount, [dict(self.evaluated, options=options)], UUID, [dict(self.raw, options=options)],
                )

    def test_usb_still_rejects_unsafe_fstab_options(self):
        for option in ("noauto", "ro", "bind", "rbind", "_netdev", "x-systemd.automount", "x-systemd.automount=yes"):
            options = f"{OPTIONS},{option}"
            with self.subTest(option=option), self.assertRaises(ValueError):
                PREFLIGHT.validate_persistence(
                    self.mount, [dict(self.evaluated, options=options)], UUID, [dict(self.raw, options=options)],
                )

    def test_usb_accepts_only_a_plain_disk_or_partition(self):
        PREFLIGHT.validate_devices([usb_disk()], {}, UUID)
        PREFLIGHT.validate_devices([usb_partition()], {}, UUID)
        for devices in (
            [], [usb_disk(), usb_disk(name="/dev/sdy")], [usb_partition(type="lvm")],
            [usb_partition(type="crypt")], [usb_partition(type="raid1")],
            [usb_partition(children=[])], [usb_partition(children=[usb_disk(), usb_disk(name="/dev/sdy")])],
            [usb_partition(children=[usb_partition()])], [usb_disk(children=[usb_disk(name="/dev/sdy")])],
            [usb_disk(tran="sata")], [usb_disk(tran="iscsi")], [usb_disk(tran=None)],
            [usb_partition(children=[usb_disk(tran="nvme")])],
        ):
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                PREFLIGHT.validate_devices(devices, {}, UUID)

    def test_internal_defaults_still_reject_usb_and_nofail(self):
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_devices([usb_partition()], {})
        with self.assertRaises(ValueError):
            PREFLIGHT.validate_persistence(self.mount, [self.evaluated])
        PREFLIGHT.validate_mount(dict(self.mount, fstype="xfs"), "/srv/crash")


class USBInspectionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.destination = str(Path(self.directory) / "kdump")
        self.mounts = [{
            "target": self.directory, "source": "/dev/sdz1", "fstype": "ext4",
            "options": "rw,relatime", "fsroot": "/", "uuid": UUID,
        }]
        self.evaluated = [dict(self.mounts[0], options=OPTIONS)]
        self.raw = [dict(self.evaluated[0], source=f"UUID={UUID}")]
        self.devices = [usb_partition()]
        self.commands = []
        self.stack.enter_context(patch.object(PREFLIGHT, "run_json", side_effect=self.run_json))
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if str(path) == self.directory:
                fields = list(result)
                fields[2] += 1000000
                return os.stat_result(fields)
            return result

        self.stack.enter_context(patch.object(Path, "stat", stat))
        self.read_text = self.stack.enter_context(patch.object(Path, "read_text", return_value="MemTotal: 100663296 kB\n"))
        self.available = self.stack.enter_context(patch.object(PREFLIGHT, "available", return_value=200 * PREFLIGHT.GIB))

    def run_json(self, argv):
        self.commands.append(argv)
        if argv[0] == "lsblk":
            return {"blockdevices": self.devices}
        if "--fstab" in argv:
            return {"filesystems": self.evaluated if "--evaluate" in argv else self.raw}
        return {"filesystems": self.mounts}

    def test_full_usb_preflight_preserves_capacity_policy(self):
        result = PREFLIGHT.inspect(self.destination, self.directory, UUID)
        self.assertEqual(result["storage_mode"], "usb-experimental")
        self.assertEqual(result["filesystem_uuid"], UUID)
        self.assertTrue(result["capacity_ok"])
        self.assertTrue(result["capacity_checked"])
        self.assertFalse(result["runtime_check"])
        self.assertEqual(result["required_bytes"], 200 * PREFLIGHT.GIB)
        self.assertEqual(self.available.call_count, 3)
        self.assertEqual(sum("--fstab" in command for command in self.commands), 2)
        self.assertEqual(sum("--evaluate" in command for command in self.commands), 1)

    def test_full_usb_preflight_rejects_insufficient_space(self):
        self.available.return_value = 199 * PREFLIGHT.GIB
        with self.assertRaisesRegex(ValueError, "two uncompressed"):
            PREFLIGHT.inspect(self.destination, self.directory, UUID)

    def test_runtime_checks_identity_but_never_reads_capacity_or_memtotal(self):
        result = PREFLIGHT.inspect(self.destination, self.directory, UUID, runtime_check=True)
        self.assertEqual(result["storage_mode"], "usb-experimental")
        self.assertTrue(result["runtime_check"])
        self.assertFalse(result["capacity_checked"])
        self.assertIsNone(result["capacity_ok"])
        self.assertNotIn("required_bytes", result)
        self.available.assert_not_called()
        self.read_text.assert_not_called()
        self.assertTrue(any(command[0] == "lsblk" for command in self.commands))
        self.assertEqual(sum("--fstab" in command for command in self.commands), 2)

    def test_runtime_rejects_absent_mount_without_capacity_checks(self):
        self.mounts[0]["target"] = "/"
        with self.assertRaisesRegex(ValueError, "not mounted"):
            PREFLIGHT.inspect(self.destination, self.directory, UUID, runtime_check=True)
        self.available.assert_not_called()

    def test_runtime_rejects_wrong_uuid_and_device(self):
        self.mounts[0]["uuid"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        with self.assertRaisesRegex(ValueError, "UUID"):
            PREFLIGHT.inspect(self.destination, self.directory, UUID, runtime_check=True)
        self.mounts[0]["uuid"] = UUID
        self.evaluated[0]["source"] = "/dev/other"
        with self.assertRaisesRegex(ValueError, "device does not match"):
            PREFLIGHT.inspect(self.destination, self.directory, UUID, runtime_check=True)
        self.available.assert_not_called()

    def test_missing_fstab_command_fails_closed(self):
        original = self.run_json

        def run_json(argv):
            if "--fstab" in argv:
                raise subprocess.CalledProcessError(1, argv)
            return original(argv)

        with patch.object(PREFLIGHT, "run_json", side_effect=run_json), self.assertRaisesRegex(ValueError, "persistent"):
            PREFLIGHT.inspect(self.destination, self.directory, UUID, runtime_check=True)

    def test_unsafe_uuid_fails_before_device_queries(self):
        with self.assertRaisesRegex(ValueError, "canonical"):
            PREFLIGHT.inspect(self.destination, self.directory, "UUID=$(id)")
        self.assertEqual(self.commands, [])

    def test_cli_exposes_usb_runtime_mode(self):
        output = io.StringIO()
        with patch.object(PREFLIGHT.sys, "argv", ["preflight", "--directory", self.destination, "--mount", self.directory,
                                                 "--usb-uuid", UUID, "--runtime-check"]), redirect_stdout(output):
            status = PREFLIGHT.main()
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["storage_mode"], "usb-experimental")
        self.available.assert_not_called()


if __name__ == "__main__":
    unittest.main()
