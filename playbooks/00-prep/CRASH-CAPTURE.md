# Worker Crash Capture

`crash-capture.yml` is a standalone staging playbook. Use it only in a
maintenance window. Ordinary server preparation and stack deployment do not
import it. This is intentional.

The playbook supports exactly one Ubuntu 24.04 amd64 K3s agent at a time. It
rejects control-plane nodes and ARM nodes.

The playbook does not do these actions:

- It does not provision disks.
- It does not reboot a host.
- It does not load a crash kernel.
- It does not intentionally crash a machine.
- It does not restart K3s.

## Storage Preflight

Before you schedule maintenance, run the read-only checks:

```bash
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/00-prep/crash-capture.yml --limit worker01.example.com \
  --tags preflight \
  -e '{"crash_capture_dump_dir":"/var/crash/kdump","crash_capture_dump_mount":"/var/crash"}'
```

The defaults require `/var/crash/kdump` on a **dedicated `/var/crash` mount**.
The playbook rejects an ordinary directory on `/`.

You can use a different dedicated filesystem that you provisioned separately.
If you do, set the dump directory and also its exact mount point. For example,
set `/srv/crash/kdump` and `/srv/crash`.

The filesystem must obey these rules:

- It must be mounted before you start.
- It must have exactly one matching writable `/etc/fstab` entry.
- Internal storage must use a mount that is not optional. USB mode has the
  explicit exception in [Experimental USB Storage](#experimental-usb-storage).
- Automount, bind, and non-fstab mounts are not supported.
- Its filesystem identity must be different from `/`. It must also be
  different from the Kubernetes/Longhorn storage that exists. This rule
  includes aliases that are mounted at a different path.

The playbook does not automatically format, resize, mount, encrypt, or select
a disk.

By default, the playbook supports only local ext4/XFS on plain SATA, SAS, or
PCIe NVMe disks. These disks must be available independently. Partitions and
LVM are permitted.

With the default settings, the playbook rejects these destinations:

- NFS, CIFS, iSCSI, and NVMe-oF.
- USB.
- Encryption.
- Bind mounts and symlinked paths.
- Unknown disk ancestry.
- Kubernetes/Longhorn storage paths and devices.

Do not use the Longhorn replica filesystem or a Longhorn volume for crash
capture. A dedicated filesystem is necessary, so that a failed dump cannot
exhaust the operating system.

The capacity must be at least **two times current MemTotal plus 8 GiB free**.
For package work, `/` must also have 2 GiB available and `/boot` must have
512 MiB available.

This rule is an intentionally conservative local policy. It is not a promise
about the size of compressed dumps. Kernel-only compression usually decreases
the size, but a damaged kernel can make the filter fail.

Noble prunes old dumps only after it saves the next dump successfully. Thus, to
keep one dump, you still need space for two dumps. For 96 GiB of memory, a
98 GiB root filesystem is not satisfactory.

This check does not reserve free space. Monitor the free space. Do the
preflight again before activation.

### Experimental USB Storage

You can test a USB SSD that you explicitly select. To do this, set
`crash_capture_usb_uuid` to the filesystem UUID of the SSD. The UUID must be
verified, canonical, and lowercase.

If you omit the value or make it empty, the internal-only defaults stay. These
defaults include the rejection of USB storage.

USB mode has these requirements:

- Dedicated ext4 on one plain USB disk/partition.
- No LVM and no encryption.
- The same full capacity checks for staging.

Provisioning and formatting are separate operator actions. This playbook never
selects or formats a disk.

The filesystem must be mounted before you start. It must have exactly one fstab
entry that is pinned to the UUID. The entry must have `nofail` and
`x-systemd.device-timeout=30s`. For example:

```fstab
UUID=<verified-ext4-uuid> /var/crash ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2
```

Add `-e crash_capture_usb_uuid=<verified-ext4-uuid>` to the preflight command
and to the staging command.

With the USB exception, the host can boot normally without the removable disk.
But the two kdump services still require its mount. Before they start, the two
services do one more identity check of the UUID, device ancestry, and fstab.

This runtime check intentionally does not do capacity checks. The capture
kernel has a smaller RAM reservation, and a dump can already exist. These
conditions must not prevent the save of the current crash. Before activation,
do the full capacity preflight again. Monitor the available space.

USB mode removes `usbcore.nousb` only from the capture arguments. Inherited
kernel arguments can disable USB or blacklist a driver. If they do, a read-only
check fails before staging.

A hook adds drivers to the kdump initramfs only. It adds and force-loads these
drivers and their dependencies:

- xHCI/EHCI.
- USB storage/UAS.
- SCSI disk.
- ext4.

If a necessary driver is not available, the hook fails. The hook does not
change the ordinary initramfs.

USB capture stays experimental until these conditions are true:

- An approved controlled crash saves a complete dump.
- The worker recovers, and console access and power access are available.
- The hardware-watchdog handoff passes the checks in
  [Hardware Watchdog](#hardware-watchdog).

## Maintenance-Gated Staging

1. Cordon the selected worker.
2. Make sure that the storage replicas are healthy and that workload failover
   operates.
3. Inhibit **all automatic reboot mechanisms**, including kured. Cordon alone
   does not inhibit kured.
4. Keep the inhibition until the separately approved reboot. Package hooks can
   create `/var/run/reboot-required`.
5. Make sure that out-of-band recovery access operates.
6. Make sure that the selected local dump filesystem is correct.
7. Before you change boot settings, keep all firmware/pstore evidence that
   exists.
8. Run staging with literal JSON booleans. These booleans acknowledge the
   prerequisites in the previous steps:

```bash
ansible-playbook -i inventory/production/hosts.yml \
  playbooks/00-prep/crash-capture.yml --limit worker01.example.com \
  -e '{"crash_capture_dump_dir":"/srv/crash/kdump","crash_capture_dump_mount":"/srv/crash","crash_capture_maintenance_confirmed":true,"crash_capture_kured_inhibited":true}'
```

The acknowledgements are statements from the operator. They are not an
automated check of the cluster lock.

Staging requires `kexec_crash_loaded=0`. To update capture that is already
armed, use a separate reviewed procedure.

Before the playbook runs apt, it applies the existing needrestart exclusions.
`policy_rc_d: 101` prevents service starts from packages.

The playbook installs `kdump-tools`, `makedumpfile`, and initramfs tooling. It
does not install the `linux-crashdump` metapackage, because that metapackage
pulls in a kernel.

Normal reboots keep their normal boot path. They do not use the kexec-tools
fast reboot.

### GRUB And Capture Initramfs

The playbook keeps the GRUB fragment that the package owns, and rebuilds GRUB.

Ubuntu Noble's reviewed `kdump-tools` version `1:1.10.3ubuntu2` uses a memory
range table. This table includes **2048M for 64-128 GiB RAM**. It does not use a
fixed 256M.

If the fragment is changed or customized, staging stops for review. The
playbook does not replace the boot arguments that exist.

Staging explicitly rebuilds the capture initramfs for the running kernel. It
uses the package kernel hook for this, and it does not load the image. Then it
makes sure that the generation was not silently skipped. For example, a skip
can occur inside a chroot.

At the first normal boot after staging, the system tries to load the capture
kernel.

### Hugepages In The Capture Kernel

Capture must not allocate the production Longhorn hugepage pool from its small
RAM reservation.

Noble's native `/etc/kdump/sysctl.conf` already sets the two hugepage
reservation controls to zero. But its initramfs script uses an invalid
`find -maxdepth=1` predicate. Because of this predicate, those overrides can
go before the production settings.

The repository owns a capture-only local-bottom helper for this problem. The
helper runs after the native hook. It copies the same override payload into
`/run/sysctl.d`. The file name of the copy sorts after the configuration that
exists.

`/proc/vmcore` guards the helper. The helper does not change package files or
the Longhorn settings for normal boot.

Before you load the rebuilt capture image, make sure that it contains these
items:

- This helper.
- The native zero-hugepage overrides.

A repeated controlled test must show zero reserved hugepages and a complete
dump. Inspection of the image alone is not end-to-end validation.

### Capture Arguments And Dump Settings

Staging explicitly restores the capture-only boot arguments of Noble and the
managed kernel/initrd paths. USB is enabled only for the explicit experimental
mode. Staging does not keep custom capture command lines and paths.

Before each changed setting, Ansible makes a timestamped backup of
`/etc/default/kdump-tools`. Keep the first backup from a run. You can use it to
restore the original configuration. Protect these backups, because they can
contain sensitive data.

The existing GRUB arguments of the normal production kernel do not change.

The dump configuration uses these settings:

- A directory that only root can access.
- A restrictive service umask.
- Kernel-page filtering/compression (`-c -d 31`).
- A saved dmesg.
- One retained successful dump.

The dump service and the load service require the selected mount. Before they
start, they assert that its exact path is a mount point. Thus, if the mount is not
there, they do not silently use the root filesystem.

Remote dump transports are explicitly disabled.

**WARNING:** Do not commit dumps to Git. Dumps can contain credentials,
personal data, and workload memory, also after filtering.

Archive the dumps in a private location. Record the checksums of the dumps.

### Journald Settings

Persistent journald configuration uses `SystemMaxUse=500M`,
`SystemKeepFree=2G`, `RuntimeMaxUse=128M`, and `SyncIntervalSec=30s`.

When the journald drop-in changes, the playbook restarts and flushes only
journald. These limits do not guarantee that the last 30 seconds stay after a
hard reset or a storage failure.

This playbook does not move or delete the pstore files that exist.

## Activation And Verification

Staged capture is not armed capture.

1. Do the storage preflight again.
2. Schedule a separate approved maintenance reboot. Reboot only one worker at a
   time.
3. After the reboot, run these commands:

```bash
cat /proc/cmdline
cat /sys/kernel/kexec_crash_loaded
sudo kdump-config show
sudo kdump-config status
sudo journalctl -u kdump-tools.service -b --no-pager
sudo journalctl --list-boots
sudo systemd-analyze cat-config systemd/journald.conf
```

4. Make sure that the output shows these results:
   - The expected `crashkernel=` argument.
   - Loaded state `1`.
   - A ready status.
5. Make sure that `/var/lib/kdump/vmlinuz` and `initrd.img` resolve to the
   running kernel.
6. Make sure that the intended filesystem is mounted.
7. Make sure that there is still enough free space.

A successful package installation alone does not show that capture is armed.
An enabled systemd service alone also does not show this.

A loaded kernel alone does not prove that end-to-end dumps operate. A
controlled crash test requires its own explicit approval. It also requires a
drained worker with console access.

For USB, do these steps:

1. Use `lsinitramfs` to examine the actual capture image.
2. Make sure that the image has the necessary drivers or their built-in
   equivalents.
3. Make sure that the pinned mounted UUID is correct.
4. Make sure that the effective runtime guards are correct.
5. Test the behavior when the removable media fails.
6. Test a full dump.

Normal Linux access to the disk does not prove that the disk operates with the
crash kernel.

### Hardware Watchdog

The existing hardware watchdog is a separate capture constraint that is not
solved. Server prep sets `RuntimeWatchdogSec=30s` and
`ShutdownWatchdogSec=10min`.

A watchdog can stay armed across panic/kexec. If it does, it can reset the host
before a large dump is complete. It can also reset the host before the capture
kernel starts to service it.

A hard reset that never gets to the kernel panic path cannot make a kdump.

This playbook does **not** disable or retune the watchdog.

1. Examine the actual device/driver timeout and takeover behavior.
2. Validate this behavior in the separately approved end-to-end test.
3. Do not call crash capture reliable before you complete steps 1 and 2.

Do not disable automatic recovery for all the cluster to make a test pass.

### After A Real Crash

1. Before you prune, copy these items off the node:
   - The dump of each real crash.
   - The kernel version.
   - The matching debug symbols.
   - The firmware/pstore record.
   - The journal of the previous boot.
2. After recovery, make sure that the node is ready.
3. Make sure that the watchdogs operate.
4. Make sure that the storage replicas are still available.
5. Uncordon the node.
   With Longhorn, replica repair can require that you restore scheduling.
6. Wait for the expected healthy replica count and for the rebuilds to
   complete.
7. Release the reboot inhibition that you intentionally kept.

## Disable Or Roll Back

1. Keep the node in coordinated maintenance.
2. Keep the automatic reboot inhibition.
3. Keep the dump directories, checksums, and pstore records that exist.
4. Keep the package/config backups.

Do not remove evidence during rollback.

To restore the configuration from before staging, do these steps:

1. Examine the original timestamped backup of `/etc/default/kdump-tools`.
2. Restore only the intended settings.
3. Restore or remove the two kdump service drop-ins of this playbook, as
   applicable.
4. Restore or remove the journald drop-in, as applicable.
5. Reload systemd.
6. Apply all journald changes during that maintenance window.

The repository owns the local-bottom helper
`/etc/initramfs-tools/scripts/local-bottom/h3xinfra-kdump-sysctl`.

- Remove it only as part of a full rollback when capture is not armed.
- If you will use the capture image again, regenerate it after you remove the
  helper.
- A change between USB and internal storage keeps this helper. The two
  capture modes need the hugepage override.

Do not remove the native `/etc/kdump/sysctl.conf` override only to restore
production hugepages. This override is already isolated from normal boot.

To prevent arming in the future, do these steps:

1. Set `USE_KDUMP=0`.
2. Disable `kdump-tools.service` without `--now`.

These steps do not unload a crash kernel that is already loaded. They also do
not release reserved RAM.

Use a separately reviewed maintenance procedure for these actions:

- Unload armed capture.
- Remove the GRUB reservation that the package owns.
- Reboot.

Do not remove the dump mount or its fstab entry while a loaded capture kernel
can still need it.

After the approved reboot, examine the effective state. Then release the reboot
inhibition.

To go back to internal storage, capture must not be armed, and the internal
preflight must pass. When you do staging without `crash_capture_usb_uuid`, it
does these actions:

- It removes the USB-specific hook and the runtime helper.
- It restores the capture arguments that disable USB.
- It regenerates the cached capture initramfs.

Do not remove the runtime helper or the USB mount while an armed USB capture
kernel can still need them.

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

The tests examine these items:

- Capacity boundaries.
- Local-device ancestry.
- Unsafe destinations.
- The single-worker/platform scope.
- Maintenance acknowledgements.
- Inhibition of package service starts.
- Absence of reboot/load commands.
- Restrictive dump settings.
- USB UUID and runtime guards.
- Rejection of inherited command lines.
- Capture-only driver hooks.

The tests never install packages, signal services, or intentionally crash a
host.
