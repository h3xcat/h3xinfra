#!/usr/bin/env python3
"""Read-only validation of a local Ubuntu kdump destination."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


GIB = 1024 ** 3
ALLOWED_PATH = re.compile(r"/[A-Za-z0-9_./-]+\Z")
USB_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


def validate_usb_uuid(usb_uuid):
    if usb_uuid is not None and not USB_UUID.fullmatch(usb_uuid):
        raise ValueError("USB mode requires a canonical lowercase filesystem UUID")


def validate_paths(destination, expected_mount):
    if expected_mount == "/":
        raise ValueError("A dedicated non-root dump filesystem is required")
    for value in (destination, expected_mount):
        if value != "/" and not ALLOWED_PATH.fullmatch(value):
            raise ValueError("Paths must be absolute and contain only letters, numbers, _, ., /, or -")
        path = Path(value)
        if ".." in path.parts or str(path) != value:
            raise ValueError("Paths must be normalized and must not contain '..'")
        if path.resolve() != path:
            raise ValueError("Symlinked dump paths and mount points are not supported")
    path, mount = Path(destination), Path(expected_mount)
    if path == mount or mount not in path.parents:
        raise ValueError("Dump directory must be below the explicitly selected mount point")
    forbidden = ("/var/lib/longhorn", "/var/lib/kubelet", "/var/lib/rancher", "/dev", "/proc", "/sys", "/run")
    for prefix in forbidden:
        if path == Path(prefix) or Path(prefix) in path.parents:
            raise ValueError("Dump destination cannot use workload storage or a virtual filesystem")


def validate_mount(mount, expected_mount, usb_uuid=None):
    validate_usb_uuid(usb_uuid)
    if mount["target"] != expected_mount:
        raise ValueError("Selected dump filesystem is not mounted at the expected mount point")
    if mount["fstype"] not in ("ext4", "xfs"):
        raise ValueError("Dump filesystem must be local ext4 or xfs")
    if usb_uuid is not None:
        if mount["fstype"] != "ext4":
            raise ValueError("Experimental USB capture requires ext4")
        if mount.get("uuid") != usb_uuid:
            raise ValueError("Mounted USB filesystem UUID does not match the selected UUID")
    if mount.get("fsroot") != "/" or "rw" not in mount["options"].split(","):
        raise ValueError("Dump filesystem must be writable and must not be a bind/subdirectory mount")
    source = mount["source"]
    if not source.startswith("/dev/") or "longhorn" in source.lower() or "[" in source:
        raise ValueError("Dump source must be an independently available local block device")


def validate_persistence(mount, fstab_entries, usb_uuid=None, raw_fstab_entries=None):
    validate_usb_uuid(usb_uuid)
    if len(fstab_entries) != 1:
        raise ValueError("Dedicated dump mount requires exactly one persistent /etc/fstab entry")
    entry = fstab_entries[0]
    if entry["target"] != mount["target"] or entry["fstype"] != mount["fstype"]:
        raise ValueError("Mounted dump filesystem does not match /etc/fstab")
    if Path(entry["source"]).resolve() != Path(mount["source"]).resolve():
        raise ValueError("Mounted dump device does not match evaluated /etc/fstab device")
    options = entry["options"].split(",")
    if any(option in ("noauto", "ro", "bind", "rbind", "_netdev")
           or option.startswith("x-systemd.automount") for option in options):
        raise ValueError("Dump mount must be a writable local boot mount without automount or bind options")
    if usb_uuid is None:
        if "nofail" in options:
            raise ValueError("Dump mount must be a required writable local boot mount")
        return
    if "nofail" not in options or "x-systemd.device-timeout=30s" not in options:
        raise ValueError("USB dump mount requires nofail and x-systemd.device-timeout=30s")
    timeouts = [option for option in options if option.startswith("x-systemd.device-timeout=")]
    if timeouts != ["x-systemd.device-timeout=30s"]:
        raise ValueError("USB dump mount must have exactly one 30s device timeout")
    if raw_fstab_entries is None or len(raw_fstab_entries) != 1:
        raise ValueError("USB dump mount requires exactly one unevaluated /etc/fstab entry")
    raw_entry = raw_fstab_entries[0]
    if raw_entry["source"] != f"UUID={usb_uuid}":
        raise ValueError("USB dump mount must use the selected UUID in /etc/fstab")
    if any(raw_entry[key] != entry[key] for key in ("target", "fstype", "options")):
        raise ValueError("Evaluated and unevaluated /etc/fstab entries do not match")


def validate_dedicated_filesystem(dump_device, workload_devices):
    if dump_device in workload_devices.values():
        raise ValueError("Dump filesystem must not share root or existing Kubernetes/Longhorn storage")


def validate_devices(devices, nvme_transports, usb_uuid=None):
    validate_usb_uuid(usb_uuid)
    if usb_uuid is not None:
        if len(devices) != 1:
            raise ValueError("USB dump source must have exactly one physical disk")
        device = devices[0]
        if device["type"] == "part":
            parents = device.get("children", [])
            if len(parents) != 1:
                raise ValueError("USB dump partition must belong to exactly one plain disk")
            device = parents[0]
        if device["type"] != "disk" or device.get("children") or device.get("tran") != "usb":
            raise ValueError("USB mode only supports a plain USB disk or one of its partitions")
        return
    disks = []

    def visit(device):
        if device["type"] not in ("disk", "part", "lvm"):
            raise ValueError("Only plain local disks, partitions, and LVM are supported")
        if device["type"] == "disk":
            disks.append(device)
        for child in device.get("children", []):
            visit(child)

    for device in devices:
        visit(device)
    if not disks:
        raise ValueError("Could not establish local physical disk ancestry")
    for disk in disks:
        transport = disk.get("tran")
        if transport not in ("nvme", "sata", "ata", "sas"):
            raise ValueError("Network, USB, or unknown disk transports are not supported")
        if transport == "nvme" and nvme_transports.get(disk["name"]) != "pcie":
            raise ValueError("Only PCIe-attached NVMe is supported, not NVMe-over-Fabrics")


def validate_capacity(memory_bytes, available_bytes, root_available, boot_available):
    # Noble purges old dumps only after saving the next one successfully.
    required = 2 * memory_bytes + 8 * GIB
    if available_bytes < required:
        raise ValueError(
            f"Dump filesystem has {available_bytes / GIB:.1f} GiB free; "
            f"requires {required / GIB:.1f} GiB (two uncompressed RAM-sized dumps + 8 GiB headroom)"
        )
    if root_available < 2 * GIB or boot_available < GIB // 2:
        raise ValueError("Staging requires at least 2 GiB free on / and 512 MiB on /boot")
    return required


def run_json(argv):
    result = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=15)
    return json.loads(result.stdout)


def available(path):
    fs = os.statvfs(path)
    return fs.f_bavail * fs.f_frsize


def inspect(destination, expected_mount, usb_uuid=None, runtime_check=False):
    validate_usb_uuid(usb_uuid)
    validate_paths(destination, expected_mount)
    existing = Path(destination)
    while not existing.exists():
        existing = existing.parent
    if not existing.is_dir():
        raise ValueError("Dump destination or its nearest existing parent is not a directory")
    mounts = run_json([
        "findmnt", "--json", "--target", str(existing),
        "--output", "TARGET,SOURCE,FSTYPE,OPTIONS,FSROOT,UUID",
    ])["filesystems"]
    if len(mounts) != 1:
        raise ValueError("Expected exactly one backing filesystem")
    mount = mounts[0]
    validate_mount(mount, expected_mount, usb_uuid)
    raw_fstab = None
    try:
        fstab = run_json([
            "findmnt", "--fstab", "--evaluate", "--json", "--mountpoint", expected_mount,
            "--output", "SOURCE,TARGET,FSTYPE,OPTIONS",
        ])["filesystems"]
        if usb_uuid is not None:
            raw_fstab = run_json([
                "findmnt", "--fstab", "--json", "--mountpoint", expected_mount,
                "--output", "SOURCE,TARGET,FSTYPE,OPTIONS",
            ])["filesystems"]
    except subprocess.CalledProcessError as error:
        raise ValueError("Dedicated dump mount requires a persistent /etc/fstab entry") from error
    validate_persistence(mount, fstab, usb_uuid, raw_fstab)
    workload_devices = {
        path: Path(path).stat().st_dev for path in
        ("/", "/var/lib/longhorn", "/var/lib/kubelet", "/var/lib/rancher")
        if Path(path).exists()
    }
    validate_dedicated_filesystem(Path(expected_mount).stat().st_dev, workload_devices)
    devices = run_json([
        "lsblk", "--json", "--inverse", "--paths", "--output", "NAME,TYPE,TRAN",
        "--", mount["source"],
    ])["blockdevices"]
    nvme_transports = {}

    def collect(device):
        if device.get("tran") == "nvme":
            transport_file = Path("/sys/class/block") / Path(device["name"]).name / "device/transport"
            nvme_transports[device["name"]] = transport_file.read_text().strip()
        for child in device.get("children", []):
            collect(child)

    for device in devices:
        collect(device)
    validate_devices(devices, nvme_transports, usb_uuid)
    result = {
        "destination": destination,
        "mount": expected_mount,
        "source": mount["source"],
        "fstype": mount["fstype"],
        "storage_mode": "usb-experimental" if usb_uuid is not None else "internal",
        "runtime_check": runtime_check,
        "capacity_checked": not runtime_check,
        "capacity_ok": None,
    }
    if usb_uuid is not None:
        result["filesystem_uuid"] = usb_uuid
    # Capture-kernel RAM is smaller, and a nearly full disk must not prevent a dump attempt.
    if runtime_check:
        return result
    memory_kib = next(
        int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal:")
    )
    free = available(existing)
    required = validate_capacity(memory_kib * 1024, free, available("/"), available("/boot"))
    result.update({
        "available_bytes": free,
        "required_bytes": required,
        "memory_bytes": memory_kib * 1024,
        "capacity_ok": True,
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--mount", required=True)
    parser.add_argument("--usb-uuid", help="Opt into experimental USB capture for this exact ext4 UUID")
    parser.add_argument("--runtime-check", action="store_true", help="Check storage identity without staging capacity thresholds")
    args = parser.parse_args()
    try:
        result = inspect(args.directory, args.mount, args.usb_uuid, args.runtime_check)
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, StopIteration) as error:
        print(json.dumps({"capacity_ok": False, "error": str(error)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
