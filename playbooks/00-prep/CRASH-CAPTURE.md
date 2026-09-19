# Worker Crash Capture

`crash-capture.yml` is a standalone, maintenance-gated staging playbook. It is
deliberately not imported by ordinary server preparation or stack deployment.
It supports exactly one Ubuntu 24.04 amd64 K3s agent at a time; control-plane
and ARM nodes are rejected. It does not provision disks, reboot a host, load a
crash kernel, deliberately crash a machine, or restart K3s.

## Storage Preflight

Run the read-only checks before scheduling maintenance:

```bash
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/00-prep/crash-capture.yml --limit worker01.example.com \
  --tags preflight \
  -e '{"crash_capture_dump_dir":"/var/crash/kdump","crash_capture_dump_mount":"/var/crash"}'
```

The defaults require `/var/crash/kdump` on a **dedicated `/var/crash` mount**.
An ordinary directory on `/` is rejected. For another separately provisioned
dedicated filesystem, set both the dump directory and its exact mount point,
for example `/srv/crash/kdump` and `/srv/crash`. The filesystem must already be
mounted and have exactly one matching writable `/etc/fstab` entry. Internal
storage requires a non-optional mount; USB mode has the explicit exception below.
Automount, bind, and non-fstab mounts are unsupported. Filesystem
identity must differ from `/` and existing Kubernetes/Longhorn storage, including
aliases mounted at a different path. The playbook
does not format, resize, mount, encrypt, or select a disk automatically.

By default, only independently available local ext4/XFS on plain SATA, SAS, or PCIe NVMe
disks (including partitions and LVM) is supported. NFS, CIFS, iSCSI, NVMe-oF,
USB, encryption, bind mounts, symlinked paths, unknown disk ancestry, and
Kubernetes/Longhorn storage paths and devices are rejected. Do not reuse the
Longhorn replica filesystem or a Longhorn volume for crash capture. A dedicated
filesystem is required so a failed dump cannot exhaust the operating system.

Capacity must be at least **two times current MemTotal plus 8 GiB free**, with
another 2 GiB available on `/` and 512 MiB on `/boot` for package work. This is
an intentionally conservative local policy, not a promise about compressed
dump sizes. Although kernel-only compression normally saves space, a damaged
kernel can defeat filtering. Noble prunes old dumps only after the next dump
has been saved successfully, so retention of one dump still needs two slots.
For 96 GiB of memory, a 98 GiB root filesystem is unsuitable. Free space is not
reserved by this check; monitor it and repeat preflight before activation.

### Experimental USB Storage

An explicitly selected USB SSD can be tested by setting
`crash_capture_usb_uuid` to its verified, canonical lowercase filesystem UUID.
An omitted or empty value retains the internal-only defaults, including rejection
of USB storage. USB mode requires dedicated ext4 on one plain USB disk/partition,
not LVM or encryption, and the same full staging capacity checks. Provisioning
and formatting remain separate operator actions; this playbook never selects or
formats a disk.

The already-mounted filesystem must have exactly one UUID-pinned fstab entry,
with `nofail` and `x-systemd.device-timeout=30s`, for example:

```fstab
UUID=<verified-ext4-uuid> /var/crash ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2
```

Pass `-e crash_capture_usb_uuid=<verified-ext4-uuid>` to both preflight and
staging commands. The USB exception permits normal boot without the removable
disk, but both kdump services still require its mount and run an additional
UUID, device-ancestry, and fstab identity check before execution. This runtime
check intentionally skips capacity checks: the capture kernel's smaller RAM
reservation and an existing dump must not prevent saving the current crash.
Repeat the full capacity preflight before activation and monitor available space.

USB mode removes `usbcore.nousb` only from the capture arguments. Inherited
USB-disable or driver-blacklist kernel arguments cause a read-only failure before
staging. A hook gated to the kdump initramfs adds and force-loads the xHCI/EHCI,
USB storage/UAS, SCSI disk, and ext4 drivers with their dependencies, failing if
a required driver is unavailable. It does not change the ordinary initramfs.
USB capture remains experimental until an approved controlled crash saves a
complete dump and the worker recovers with console and power access available.
The hardware-watchdog handoff must also pass the checks below.

## Maintenance-Gated Staging

1. Cordon the selected worker, confirm healthy storage replicas and workload
   failover, and inhibit **all automatic reboot mechanisms**, including kured.
   Cordon alone does not inhibit kured. Keep the inhibition until the separately
   approved reboot; package hooks can create `/var/run/reboot-required`.
2. Confirm out-of-band recovery access and the selected local dump filesystem.
   Retain any existing firmware/pstore evidence before changing boot settings.
3. Run staging with literal JSON booleans acknowledging those prerequisites:

```bash
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/00-prep/crash-capture.yml --limit worker01.example.com \
  -e '{"crash_capture_dump_dir":"/srv/crash/kdump","crash_capture_dump_mount":"/srv/crash","crash_capture_maintenance_confirmed":true,"crash_capture_kured_inhibited":true}'
```

The acknowledgements are operator assertions, not an automated check of the
cluster lock. Staging requires `kexec_crash_loaded=0`; updating already armed
capture needs a separate reviewed procedure. Existing needrestart exclusions
are applied before apt. `policy_rc_d: 101` inhibits package service starts;
the playbook installs `kdump-tools`, `makedumpfile`, and initramfs tooling,
not the kernel-pulling `linux-crashdump` metapackage. Normal reboots retain
their normal boot path, rather than using kexec-tools fast reboot.

The playbook preserves the package-owned GRUB fragment and rebuilds GRUB.
Ubuntu Noble's reviewed `kdump-tools` version `1:1.10.3ubuntu2` uses a memory
range table, including **2048M for 64-128 GiB RAM**, not a fixed 256M. A changed
or customized fragment causes staging to stop for review. Existing boot
arguments are not replaced. Staging explicitly rebuilds the running kernel's
capture initramfs using the package kernel hook, without loading it, and checks
that generation was not silently skipped (for example, inside a chroot).
The first subsequent normal boot attempts to load the capture kernel.

Capture must not allocate the production Longhorn hugepage pool from its small
RAM reservation. Noble's native `/etc/kdump/sysctl.conf` already sets the two
hugepage reservation controls to zero, but its initramfs script uses an invalid
`find -maxdepth=1` predicate that can place those overrides before production
settings. A repository-owned, capture-only local-bottom helper runs after the
native hook and copies that same override payload into `/run/sysctl.d` under a
name sorting after existing configuration. It is guarded by `/proc/vmcore` and
does not modify package files or normal-boot Longhorn settings. Verify the rebuilt
capture image contains this helper and the native zero-hugepage overrides before
loading it. A repeated controlled test must verify zero reserved hugepages and
a complete dump; image inspection alone is not end-to-end validation.

Staging explicitly restores Noble's capture-only boot arguments and managed
kernel/initrd paths, with USB enabled only for the explicit experimental mode;
custom capture command lines and paths are not retained.
Ansible creates timestamped backups of `/etc/default/kdump-tools` before each
changed setting; retain the first backup from a run to restore the original
configuration, and protect these backups as potentially sensitive files.
The normal production kernel's existing GRUB arguments remain unchanged.
Dump configuration uses a root-only directory, restrictive service umask,
kernel-page filtering/compression (`-c -d 31`), saved dmesg, and one retained
successful dump. The dump and loading services require the selected mount and
assert that its exact path is a mount point before executing, preventing a
missing mount from silently falling back to the root filesystem.
Remote dump transports are explicitly disabled. Dumps can contain credentials,
personal data, and workload memory despite filtering; archive them privately,
record checksums, and never commit them to Git.

Persistent journald configuration uses `SystemMaxUse=500M`,
`SystemKeepFree=2G`, `RuntimeMaxUse=128M`, and `SyncIntervalSec=30s`.
Only journald is restarted and flushed when its drop-in changes. These bounds
do not guarantee the last 30 seconds survive a hard reset or storage failure.
Existing pstore files are neither moved nor deleted by this playbook.

## Activation And Verification

Staged is not armed. Schedule a separate approved, one-worker-at-a-time
maintenance reboot after repeating storage preflight. Verify afterward:

```bash
cat /proc/cmdline
cat /sys/kernel/kexec_crash_loaded
sudo kdump-config show
sudo kdump-config status
sudo journalctl -u kdump-tools.service -b --no-pager
sudo journalctl --list-boots
sudo systemd-analyze cat-config systemd/journald.conf
```

Require the expected `crashkernel=` argument, loaded state `1`, and a ready
status. Also check `/var/lib/kdump/vmlinuz` and `initrd.img` resolve to the
running kernel, the intended filesystem is mounted, and free space remains
sufficient. A package installed successfully or an enabled systemd service
alone does not demonstrate that capture is armed. A loaded kernel alone does
not prove end-to-end dumping works; a controlled crash test requires its own
explicit approval and a drained worker with console access.

For USB, inspect the actual capture image with `lsinitramfs` and verify the
required drivers (or their built-in equivalents), the pinned mounted UUID, and
the effective runtime guards. Test removable-media failure behavior and a full
dump; normal Linux access to the disk does not prove crash-kernel compatibility.

The existing hardware watchdog is a separate unresolved capture constraint.
Server prep configures `RuntimeWatchdogSec=30s` and
`ShutdownWatchdogSec=10min`. A watchdog that remains armed across panic/kexec
can reset the host before a large dump finishes or before the capture kernel
starts servicing it. A hard reset that never reaches the kernel panic path
cannot produce a kdump at all. This playbook does **not** disable or retune
the watchdog. Review the actual device/driver timeout and takeover behavior,
then validate it in the separately approved end-to-end test before describing
crash capture as reliable. Do not disable automatic recovery cluster-wide to
make a test pass.

Preserve each real crash's dump, kernel version, matching debug symbols,
firmware/pstore record, and previous-boot journal off-node before pruning.
After recovery, verify node readiness, watchdogs and surviving storage replicas
before uncordoning. With Longhorn, restoring scheduling may be necessary for
replica repair. Wait for the expected healthy replica count and completed
rebuilds before releasing the deliberately retained reboot inhibition.

## Disabling Or Rolling Back

Keep the node in coordinated maintenance and retain automatic reboot inhibition.
Preserve existing dump directories, checksums, pstore records, and package/config
backups; rollback must not remove evidence. To restore pre-staging configuration,
review the original timestamped `/etc/default/kdump-tools` backup and restore
only the intended settings. Restore or remove this playbook's two kdump service
drop-ins and journald drop-in as appropriate, then reload systemd and apply any
journald change during that maintenance window.

The repository-owned local-bottom helper is
`/etc/initramfs-tools/scripts/local-bottom/h3xinfra-kdump-sysctl`. Remove it only
as part of a full, unarmed rollback and regenerate the capture image if it will
still be used. Switching between USB and internal storage keeps this helper:
both capture modes need the hugepage override. Never remove the native
`/etc/kdump/sysctl.conf` override merely to restore production hugepages; it is
already isolated from normal boot.

To prevent future arming, set `USE_KDUMP=0` and disable `kdump-tools.service`
without `--now`. This does not unload an already loaded crash kernel or release
reserved RAM. Unloading armed capture, removing the package-owned GRUB
reservation, and rebooting all need a separately reviewed maintenance procedure.
Do not remove the dump mount or its fstab entry while a loaded capture kernel
may still need it. Verify effective state after the approved reboot before
releasing the reboot inhibition.

Returning to internal storage requires unarmed capture and a passing internal
preflight. Staging without `crash_capture_usb_uuid` removes the USB-specific hook
and runtime helper, restores the USB-disabled capture arguments, and regenerates
the cached capture initramfs. Do not remove the runtime helper or USB mount while
an armed USB capture kernel may still need them.

## References And Tests

- [Ubuntu kernel crash dump guide](https://documentation.ubuntu.com/server/how-to/software/kernel-crash-dump/index.html)
- [Official Noble package source](https://archive.ubuntu.com/ubuntu/pool/main/k/kdump-tools/kdump-tools_1.10.3ubuntu2.tar.xz)
- [Noble package details](https://packages.ubuntu.com/noble/kdump-tools)
- [Linux kdump documentation](https://www.kernel.org/doc/html/latest/admin-guide/kdump/kdump.html)
- [systemd journald configuration](https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html)

```bash
python3 -m unittest discover -s playbooks/00-prep/tests -v
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/00-prep/crash-capture.yml --syntax-check
```

The tests cover capacity boundaries, local-device ancestry, unsafe destinations,
single-worker/platform scope, maintenance acknowledgements, package service
inhibition, absence of reboot/load commands, restrictive dump settings, USB UUID
and runtime guards, inherited command-line rejection, and capture-only driver hooks.
They never install packages, signal services, or intentionally crash a host.
