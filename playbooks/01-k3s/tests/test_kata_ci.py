"""Static checks for kata-ci.yml (Kata Containers runtime for CI pods).

The play vars and the task templates are rendered with plain Jinja2 plus
small stand-ins for the Ansible filters and tests that they use. No hosts are
contacted. The service state and the guarded restart come from the shared
task files tasks/k3s-service-state.yml and tasks/restart-k3s.yml.
"""

import json
from pathlib import Path
import sys
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from playbook_helpers import (  # noqa: E402
    IMPORT, LAYER, environment, evaluate, flatten, load, render_vars, service_facts, when_true,
)


PLAYBOOK = LAYER / "kata-ci.yml"
TASKS = LAYER / "tasks" / "kata-ci.yml"
GROUPS = {"server": ["s1", "s2", "s3"], "agent": ["a1"], "k3s_cluster": ["s1", "s2", "s3", "a1"]}
RESTART_TASK = "Restart K3s to load the new configuration"
KATA_VERSION = "4.4.0"
# GitHub release metadata of kata-containers 4.4.0, asset
# kata-static-4.4.0-amd64.tar.zst.
KATA_SHA256 = "ccbc0cbc591089d0c57012b78da64b23ef43e0bac0f869dc7bfebe8588c33986"
SHIM = "/opt/kata/runtime-rs/bin/containerd-shim-kata-v2"
QEMU = "/opt/kata/bin/qemu-system-x86_64"
VIRTIOFSD = "/opt/kata/libexec/virtiofsd"
QEMU_CONFIG = "/opt/kata/share/defaults/kata-containers/runtime-rs/configuration-qemu-runtime-rs.toml"
CONTAINERD_DROPIN = "/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/50-kata-ci.toml"
SECCOMP = "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"

# The kata-ci runtime as `k3s crictl info` shows it (containerd 2.3 JSON
# names of the CRI runtime fields), with the values of the drop-in.
LIVE_RUNTIME = {
    "runtimeType": "io.containerd.kata.v2",
    "runtimePath": SHIM,
    "PodAnnotations": [],
    "ContainerAnnotations": [],
    "options": {"ConfigPath": QEMU_CONFIG},
    "privileged_without_host_devices": True,
    "privileged_without_host_devices_all_devices_allowed": False,
    "baseRuntimeSpec": "",
    "cniConfDir": "",
    "cniMaxConfNum": 0,
    "snapshotter": "",
    "sandboxer": "podsandbox",
    "io_type": "",
}


def crictl_info(runtime=None):
    runtimes = {"runc": {"runtimeType": "io.containerd.runc.v2", "options": {"SystemdCgroup": True}}}
    if runtime is not None:
        runtimes["kata-ci"] = runtime
    info = {"status": {"conditions": []}, "config": {"containerd": {"runtimes": runtimes}}, "golang": "go1.25"}
    return {"rc": 0, "stdout": json.dumps(info)}


def crictl_version(runtime_version):
    lines = ["Version:  0.1.0", "RuntimeName:  containerd", f"RuntimeVersion:  {runtime_version}",
             "RuntimeApiVersion:  v1"]
    return {"rc": 0, "stdout": "\n".join(lines), "stdout_lines": lines}


class KataCiPlaybook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plays = load(PLAYBOOK)
        cls.vars = cls.plays[1]["vars"]
        cls.own_tasks = load(TASKS)
        cls.tasks = flatten(TASKS)

    def context(self, enabled=True, host="a1", state="active", live=..., release=True, check_mode=False,
                **registered):
        """Render the play vars on host as Ansible would, then add the restart decision."""
        context = {"inventory_hostname": host, "groups": GROUPS, "ansible_check_mode": check_mode,
                   "kata_ci_enabled": enabled, "kata_version": KATA_VERSION, "kata_sha256": KATA_SHA256,
                   "kata_ci_release": {"stat": {"exists": release}}, **service_facts(state), **registered}
        if state == "active":
            context["kata_ci_crictl_info"] = crictl_info(LIVE_RUNTIME if live is ... else live)
        else:
            context["kata_ci_crictl_info"] = {"skipped": True}
        context = render_vars(self.vars, context)
        decide = self.task("Decide whether K3s must restart")["ansible.builtin.set_fact"]
        return render_vars(decide, context)

    def task(self, name):
        return next(t for t in self.tasks if t["name"] == name)

    def index(self, name):
        return next(i for i, t in enumerate(self.tasks) if t["name"] == name)

    def render(self, template, context):
        return environment(native=False).from_string(template).render(context)


class ValidationTests(KataCiPlaybook):
    def check(self, enabled=True, **values):
        task = self.plays[0]["tasks"][0]
        context = {"inventory_hostname": "a1", "kata_ci_enabled": enabled, **values}
        if not when_true(task, context):
            return None
        return all(evaluate(c, **context) for c in task["ansible.builtin.assert"]["that"])

    def test_valid_pins_pass(self):
        self.assertIs(self.check(kata_version="4.4.0", kata_sha256=KATA_SHA256), True)
        self.assertIs(self.check(kata_version="10.0.12", kata_sha256="0" * 64), True)

    def test_invalid_pins_fail(self):
        for values in ({}, {"kata_version": "4.4.0"}, {"kata_sha256": KATA_SHA256},
                       {"kata_version": "4.4", "kata_sha256": KATA_SHA256},
                       {"kata_version": "v4.4.0", "kata_sha256": KATA_SHA256},
                       {"kata_version": "4.4.0'; rm -rf /; '", "kata_sha256": KATA_SHA256},
                       {"kata_version": "4.4.0/../x", "kata_sha256": KATA_SHA256},
                       {"kata_version": "4.4.0", "kata_sha256": KATA_SHA256.upper()},
                       {"kata_version": "4.4.0", "kata_sha256": "sha256:" + KATA_SHA256},
                       {"kata_version": "4.4.0", "kata_sha256": KATA_SHA256[:63]}):
            with self.subTest(values=values):
                self.assertIs(self.check(**values), False)

    def test_disabled_nodes_need_no_pins(self):
        for enabled in (False, "false", None):
            with self.subTest(enabled=enabled):
                self.assertIsNone(self.check(enabled=enabled))

    def test_validation_runs_first_on_every_node(self):
        play = self.plays[0]
        self.assertEqual(play["hosts"], "k3s_cluster")
        self.assertIs(play["any_errors_fatal"], True)
        modules = {next(k for k in t if k.startswith("ansible.builtin.")) for t in play["tasks"]}
        self.assertEqual(modules, {"ansible.builtin.assert"})


class PlaybookTests(KataCiPlaybook):
    def test_servers_then_agents_one_at_a_time(self):
        servers, agents = self.plays[1], self.plays[2]
        self.assertEqual((servers["hosts"], agents["hosts"]), ("server", "agent"))
        for play in (servers, agents):
            with self.subTest(play=play["name"]):
                self.assertEqual(play["serial"], 1)
                self.assertEqual(play["max_fail_percentage"], 0)
                self.assertIs(play["become"], True)
                self.assertIs(play["gather_facts"], False)
                self.assertEqual(play["vars"], self.vars)
                self.assertEqual(play["tasks"], [{"name": "Apply the Kata CI runtime", IMPORT: "tasks/kata-ci.yml"}])

    def test_unit_names(self):
        self.assertEqual(self.context(host="s2")["kata_ci_unit_name"], "k3s")
        self.assertEqual(self.context(host="a1")["kata_ci_unit_name"], "k3s-agent")
        self.assertEqual(self.task("Read the K3s service state")["vars"], {"k3s_service_name": "{{ kata_ci_unit_name }}"})

    def test_release_paths(self):
        context = self.context()
        self.assertEqual(context["kata_ci_release_dir"], "/opt/kata-releases/4.4.0")
        self.assertEqual(context["kata_ci_archive"], "/opt/kata-releases/kata-static-4.4.0-amd64.tar.zst")
        self.assertEqual(context["kata_ci_url"], "https://github.com/kata-containers/kata-containers/releases/"
                                                 "download/4.4.0/kata-static-4.4.0-amd64.tar.zst")
        self.assertEqual((context["kata_ci_shim"], context["kata_ci_qemu"], context["kata_ci_virtiofsd"],
                          context["kata_ci_config"]), (SHIM, QEMU, VIRTIOFSD, QEMU_CONFIG))
        self.assertEqual(context["kata_ci_containerd_dropin"], CONTAINERD_DROPIN)
        # The shim canonicalizes the configuration path and reads config.d
        # next to the real file: inside the release, not under /opt/kata.
        real_config = QEMU_CONFIG.replace("/opt/kata/", "/opt/kata-releases/4.4.0/", 1)
        self.assertEqual(context["kata_ci_kata_dropin"],
                         str(Path(real_config).parent / "config.d" / "50-h3xinfra-kata-ci.toml"))

    def test_checked_files_are_the_files_of_the_runtime(self):
        task = self.task("Read the Kata files of the kata-ci runtime")
        files = ["/opt/kata/" + item for item in task["loop"]]
        for path in (SHIM, QEMU, VIRTIOFSD, QEMU_CONFIG):
            with self.subTest(path=path):
                self.assertIn(path, files)
        # The kernel and image that the shipped QEMU configuration names.
        self.assertIn("/opt/kata/share/kata-containers/vmlinux.container", files)
        self.assertIn("/opt/kata/share/kata-containers/kata-containers.img", files)
        self.assertIs(task["ansible.builtin.stat"]["follow"], True)

    def test_read_only_commands_never_report_changes(self):
        for task in self.tasks:
            if "ansible.builtin.command" in task:
                with self.subTest(task=task["name"]):
                    self.assertIs(task["changed_when"], False)


class DisabledTests(KataCiPlaybook):
    # The tasks that may run on a node without kata_ci_enabled.
    ALLOWED = {"Read the K3s service state", "Record the K3s service state",
               "Refuse a K3s service that is failing or changing state",
               "Remove the containerd drop-in for the kata-ci runtime",
               "Read the runtimes of the running containerd", "Decide whether K3s must restart",
               "Read the runtimes of the containerd after the restart",
               "Check the kata-ci runtime of the running containerd"}

    def test_only_the_rollback_runs(self):
        context = self.context(enabled=False, live=None)
        ran = {t["name"] for t in self.tasks if when_true(t, context)}
        self.assertEqual(ran, self.ALLOWED)

    def test_nothing_is_installed(self):
        context = self.context(enabled=False, live=None)
        for task in self.tasks:
            module = next(k for k in task if k.startswith(("ansible.", "community.")))
            if module in ("ansible.builtin.get_url", "ansible.builtin.shell", "ansible.builtin.copy",
                          "ansible.builtin.apt", "community.general.modprobe"):
                with self.subTest(task=task["name"]):
                    self.assertFalse(when_true(task, context))

    def test_removal_and_write_exclude_each_other(self):
        write = self.task("Write the containerd drop-in for the kata-ci runtime")
        remove = self.task("Remove the containerd drop-in for the kata-ci runtime")
        self.assertEqual(remove["ansible.builtin.file"], {"path": "{{ kata_ci_containerd_dropin }}", "state": "absent"})
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                context = self.context(enabled=enabled)
                self.assertIs(when_true(write, context), enabled)
                self.assertIs(when_true(remove, context), not enabled)

    def test_removal_comes_before_the_service_check(self):
        # A drop-in that stops K3s makes the service check fail. The removal
        # must run before it, and it must not depend on the service state.
        remove = self.task("Remove the containerd drop-in for the kata-ci runtime")
        self.assertEqual(self.tasks[0], remove)
        self.assertLess(self.index(remove["name"]), self.index("Refuse a K3s service that is failing or changing state"))
        self.assertEqual(remove["when"], "not (kata_ci_on | bool)")

    def test_stopped_k3s_is_not_read(self):
        context = self.context(enabled=False, state="inactive")
        for name in ("Read the runtimes of the running containerd", "Read the runtimes of the containerd after the restart",
                     "Check the kata-ci runtime of the running containerd"):
            with self.subTest(task=name):
                self.assertFalse(when_true(self.task(name), context))


class CheckTests(KataCiPlaybook):
    def assert_task(self, name, **context_kwargs):
        that = self.task(name)["ansible.builtin.assert"]["that"]
        context = self.context(**context_kwargs)
        return all(evaluate(c, **context) for c in (that if isinstance(that, list) else [that]))

    def test_x86_64_with_kvm_only(self):
        name = "Refuse a node without x86_64 or KVM"
        self.assertTrue(self.assert_task(name, kata_ci_arch={"stdout": "x86_64\n"},
                                         kata_ci_kvm={"stat": {"exists": True, "ischr": True}}))
        self.assertFalse(self.assert_task(name, kata_ci_arch={"stdout": "aarch64"},
                                          kata_ci_kvm={"stat": {"exists": True, "ischr": True}}))
        self.assertFalse(self.assert_task(name, kata_ci_arch={"stdout": "x86_64"}, kata_ci_kvm={"stat": {"exists": False}}))

    def test_containerd_2_3_or_later(self):
        name = "Refuse a containerd older than 2.3"
        for version, ok in (("v2.3.7-k3s1", True), ("2.3.0", True), ("v2.4.0-k3s1", True), ("v3.0.1", True),
                            ("v2.2.3-k3s1", False), ("v1.7.27-k3s1", False)):
            with self.subTest(version=version):
                self.assertIs(self.assert_task(name, kata_ci_crictl_version=crictl_version(version)), ok)
        self.assertFalse(self.assert_task(name, kata_ci_crictl_version={"rc": 0, "stdout_lines": []}))
        context = self.context(kata_ci_crictl_version=crictl_version("v2.3.7-k3s1"))
        self.assertEqual(context["kata_ci_containerd_version"], "2.3.7-k3s1")

    def test_containerd_version_needs_a_running_k3s(self):
        for name in ("Read the containerd version", "Refuse a containerd older than 2.3"):
            with self.subTest(task=name):
                self.assertTrue(when_true(self.task(name), self.context()))
                self.assertFalse(when_true(self.task(name), self.context(state="inactive")))

    def test_checks_come_before_the_first_change(self):
        # The changes on a node with kata_ci_enabled. The rollback removal
        # runs only without it.
        context = self.context(release=False)
        changes = [i for i, t in enumerate(self.tasks)
                   if any(k in t for k in ("ansible.builtin.apt", "ansible.builtin.file", "ansible.builtin.get_url",
                                           "ansible.builtin.shell", "ansible.builtin.copy",
                                           "community.general.modprobe"))
                   and when_true(t, context)]
        for name in ("Refuse a K3s service that is failing or changing state", "Refuse a node without x86_64 or KVM",
                     "Refuse a containerd older than 2.3"):
            with self.subTest(check=name):
                self.assertLess(self.index(name), min(changes))

    def test_free_space(self):
        name = "Refuse the download with less than 10 GiB free under /opt"
        for avail, ok in (("10240", True), ("51200", True), ("10239", False)):
            with self.subTest(avail=avail):
                free = {"stdout_lines": ["Avail", avail]}
                self.assertIs(self.assert_task(name, release=False, kata_ci_free=free), ok)
        self.assertEqual(self.task("Read the free disk space under /opt")["ansible.builtin.command"]["argv"],
                         ["df", "--output=avail", "--block-size=1M", "/opt"])


class InstallTests(KataCiPlaybook):
    DOWNLOAD_STEPS = ("Read the free disk space under /opt", "Refuse the download with less than 10 GiB free under /opt",
                      "Install zstd for the Kata release archive", "Download the Kata release archive")

    def test_download_only_without_the_release(self):
        for name in self.DOWNLOAD_STEPS:
            with self.subTest(task=name):
                self.assertTrue(when_true(self.task(name), self.context(release=False,
                                                                         kata_ci_free={"stdout_lines": ["Avail", "51200"]})))
                self.assertFalse(when_true(self.task(name), self.context(release=True)))

    def test_download_checks_the_pinned_sha256(self):
        get_url = self.task("Download the Kata release archive")["ansible.builtin.get_url"]
        self.assertEqual(get_url["url"], "{{ kata_ci_url }}")
        self.assertEqual(get_url["dest"], "{{ kata_ci_archive }}")
        self.assertEqual(self.render(get_url["checksum"], self.context()), "sha256:" + KATA_SHA256)

    def test_extraction_is_complete_or_absent(self):
        task = self.task("Extract the Kata release")
        shell = task["ansible.builtin.shell"]
        self.assertEqual(shell["creates"], "{{ kata_ci_release_dir }}")
        script = self.render(shell["cmd"], self.context())
        lines = [line for line in script.splitlines() if line]
        self.assertEqual(lines[0], "set -eu")
        self.assertIn("tar --zstd -xf '/opt/kata-releases/kata-static-4.4.0-amd64.tar.zst' "
                      "-C '/opt/kata-releases/4.4.0.partial' --no-same-owner --strip-components=3 ./opt/kata", lines)
        self.assertEqual(lines[-1], "mv '/opt/kata-releases/4.4.0.partial' '/opt/kata-releases/4.4.0'")
        self.assertLess(self.index("Download the Kata release archive"), self.index("Extract the Kata release"))
        self.assertLess(self.index("Extract the Kata release"), self.index("Remove the Kata release archive"))

    def test_release_files_are_checked_before_use(self):
        verify = self.task("Refuse a Kata release without these files")
        results = [{"item": "bin/qemu-system-x86_64", "stat": {"exists": True}},
                   {"item": "libexec/virtiofsd", "stat": {"exists": False}}]
        context = self.context(kata_ci_files={"results": results})
        self.assertFalse(evaluate(verify["ansible.builtin.assert"]["that"], **context))
        results[1]["stat"]["exists"] = True
        self.assertTrue(evaluate(verify["ansible.builtin.assert"]["that"], **context))
        first_use = min(self.index(n) for n in ("Create the config.d directory of the Kata release",
                                                "Write the Kata hardening drop-in", "Point /opt/kata to the Kata release",
                                                "Write the containerd drop-in for the kata-ci runtime"))
        self.assertLess(self.index("Refuse a Kata release without these files"), first_use)

    def test_symlink_after_the_hardening_dropin(self):
        link = self.task("Point /opt/kata to the Kata release")["ansible.builtin.file"]
        self.assertEqual(link, {"src": "{{ kata_ci_release_dir }}", "dest": "/opt/kata", "state": "link"})
        self.assertLess(self.index("Write the Kata hardening drop-in"), self.index("Point /opt/kata to the Kata release"))
        self.assertLess(self.index("Point /opt/kata to the Kata release"),
                        self.index("Write the containerd drop-in for the kata-ci runtime"))

    def test_check_mode_skips_the_files_of_a_missing_release(self):
        for name in ("Read the Kata files of the kata-ci runtime", "Refuse a Kata release without these files",
                     "Create the config.d directory of the Kata release", "Write the Kata hardening drop-in",
                     "Point /opt/kata to the Kata release"):
            task = self.task(name)
            with self.subTest(task=name):
                self.assertTrue(when_true(task, self.context(release=False)))
                self.assertTrue(when_true(task, self.context(release=True, check_mode=True)))
                self.assertFalse(when_true(task, self.context(release=False, check_mode=True)))

    def test_dropin_directories_are_created_first(self):
        # The release archive has no config.d directory, and K3s does not
        # create config-v3.toml.d. copy fails when the directory is missing.
        context = self.context()
        copies = [i for i, t in enumerate(self.tasks) if "ansible.builtin.copy" in t]
        self.assertEqual(len(copies), 2)
        for i in copies:
            copy = self.tasks[i]
            dest = self.render(copy["ansible.builtin.copy"]["dest"], context)
            with self.subTest(task=copy["name"]):
                directory = self.tasks[i - 1]
                self.assertEqual(directory["ansible.builtin.file"]["state"], "directory")
                self.assertEqual(self.render(directory["ansible.builtin.file"]["path"], context), str(Path(dest).parent))
                self.assertEqual((directory["ansible.builtin.file"]["owner"], directory["ansible.builtin.file"]["group"],
                                  directory["ansible.builtin.file"]["mode"]), ("root", "root", "0755"))
                self.assertEqual(directory["when"], copy["when"])
        self.assertEqual(self.render(self.task("Create the containerd drop-in directory")["ansible.builtin.file"]["path"],
                                     context), str(Path(CONTAINERD_DROPIN).parent))

    def test_vhost_modules_load_now_and_at_boot(self):
        task = self.task("Load the vhost modules now and at boot")
        self.assertEqual(task["loop"], ["vhost_vsock", "vhost_net"])
        self.assertEqual(task["community.general.modprobe"],
                         {"name": "{{ item }}", "state": "present", "persistent": "present"})


class KataDropinTests(KataCiPlaybook):
    def test_hardening_settings(self):
        copy = self.task("Write the Kata hardening drop-in")["ansible.builtin.copy"]
        self.assertEqual(copy["dest"], "{{ kata_ci_kata_dropin }}")
        self.assertEqual((copy["owner"], copy["group"], copy["mode"]), ("root", "root", "0644"))
        rendered = self.render(copy["content"], self.context())
        self.assertTrue(rendered.startswith("# Managed by h3xinfra"))
        self.assertEqual(tomllib.loads(rendered), {
            "hypervisor": {"qemu": {
                "path": QEMU,
                "valid_hypervisor_paths": [QEMU],
                "virtio_fs_daemon": VIRTIOFSD,
                "valid_virtio_fs_daemon_paths": [VIRTIOFSD],
                "enable_annotations": [],
                "rootless": False,
                "seccomp_sandbox": SECCOMP,
                "cpu_features": "pmu=off,vmx=off",
            }},
            "agent": {"kata": {"debug_console_enabled": False}},
            "runtime": {"emptydir_mode": "block-plain"},
        })


class ContainerdDropinTests(KataCiPlaybook):
    def runtime_table(self):
        copy = self.task("Write the containerd drop-in for the kata-ci runtime")["ansible.builtin.copy"]
        self.assertEqual(copy["dest"], "{{ kata_ci_containerd_dropin }}")
        self.assertEqual((copy["owner"], copy["group"], copy["mode"]), ("root", "root", "0644"))
        config = tomllib.loads(self.render(copy["content"], self.context()))
        # Without a version, containerd migrates the file from version 1.
        self.assertEqual(config["version"], 3)
        self.assertEqual(list(config), ["version", "plugins"])
        self.assertEqual(list(config["plugins"]), ["io.containerd.cri.v1.runtime"])
        return config["plugins"]["io.containerd.cri.v1.runtime"]["containerd"]["runtimes"]

    def test_runtime_handler(self):
        runtimes = self.runtime_table()
        self.assertEqual(list(runtimes), ["kata-ci"])
        self.assertEqual(runtimes["kata-ci"], {
            "runtime_type": "io.containerd.kata.v2",
            "runtime_path": SHIM,
            "privileged_without_host_devices": True,
            "pod_annotations": [],
            "container_annotations": [],
            "options": {"ConfigPath": QEMU_CONFIG},
        })

    def test_expected_runtime_is_the_dropin(self):
        runtime = dict(self.runtime_table()["kata-ci"])
        runtime["config_path"] = runtime.pop("options")["ConfigPath"]
        self.assertEqual(self.context()["kata_ci_expected"], runtime)
        self.assertIsNone(self.context(enabled=False)["kata_ci_expected"])

    def test_loaded_runtime_from_crictl_info(self):
        self.assertEqual(self.context()["kata_ci_loaded"], self.context()["kata_ci_expected"])
        self.assertIsNone(self.context(live=None)["kata_ci_loaded"])
        # containerd prints a nil list as null.
        nulls = {**LIVE_RUNTIME, "PodAnnotations": None, "ContainerAnnotations": None}
        self.assertEqual(self.context(live=nulls)["kata_ci_loaded"], self.context()["kata_ci_expected"])
        self.assertIsNone(self.context(state="inactive")["kata_ci_loaded"])


class RestartTests(KataCiPlaybook):
    def restart(self, **kwargs):
        return self.context(**kwargs)["kata_ci_restart"]

    def test_restart_decision(self):
        # New node in the kata-ci group, or an earlier run that stopped
        # before the restart.
        self.assertIs(self.restart(live=None), True)
        self.assertIs(self.restart(), False)
        self.assertIs(self.restart(live={**LIVE_RUNTIME, "runtimePath": "/usr/local/bin/containerd-shim-kata-v2"}), True)
        self.assertIs(self.restart(live={**LIVE_RUNTIME, "privileged_without_host_devices": False}), True)
        self.assertIs(self.restart(live={**LIVE_RUNTIME, "PodAnnotations": ["io.katacontainers.*"]}), True)
        self.assertIs(self.restart(live={**LIVE_RUNTIME, "options": {}}), True)
        # Rollback: the drop-in is gone, but containerd still has the runtime.
        self.assertIs(self.restart(enabled=False), True)
        self.assertIs(self.restart(enabled=False, live=None), False)
        # K3s is not running (new node, or stopped): never started here.
        self.assertIs(self.restart(state="inactive"), False)
        self.assertIs(self.restart(enabled=False, state="inactive"), False)

    def test_restart_follows_the_decision(self):
        restart = self.task(RESTART_TASK)
        self.assertEqual(restart["when"], ["k3s_restart | bool"])
        self.assertEqual(restart["vars"], {"k3s_service_name": "{{ kata_ci_unit_name }}", "k3s_restart": "{{ kata_ci_restart }}"})
        self.assertEqual(restart["timeout"], 600)
        self.assertTrue(when_true(restart, self.context(live=None)))
        self.assertFalse(when_true(restart, self.context()))

    def test_decision_is_fixed_before_the_restart(self):
        imports = [t[IMPORT] for t in self.own_tasks if IMPORT in t]
        self.assertEqual(imports, ["k3s-service-state.yml", "restart-k3s.yml"])
        decide = self.index("Decide whether K3s must restart")
        self.assertLess(self.index("Read the runtimes of the running containerd"), decide)
        self.assertLess(self.index("Write the containerd drop-in for the kata-ci runtime"), decide)
        self.assertLess(self.index("Remove the containerd drop-in for the kata-ci runtime"), decide)
        self.assertLess(decide, self.index("Check that the API server on every other server is ready"))
        read = self.task("Read the runtimes of the running containerd")
        self.assertEqual(read["ansible.builtin.command"]["argv"], ["k3s", "crictl", "info", "-o", "json"])
        self.assertIs(read["check_mode"], False)

    def test_restart_is_guarded(self):
        for name in ("Check that the API server on every other server is ready",
                     "Check that every etcd node is Ready and not cordoned",
                     "Check that kured is not rebooting a node"):
            with self.subTest(gate=name):
                self.assertLess(self.index(name), self.index(RESTART_TASK))
                self.assertIn("k3s_restart | bool", self.task(name)["when"])
        self.assertLess(self.index(RESTART_TASK), self.index("Wait for the node to be Ready"))

    def test_checks_after_the_restart(self):
        after = ("Read the runtimes of the containerd after the restart",
                 "Count the drop-in imports in the generated containerd configuration",
                 "Check that containerd imports the drop-in directory",
                 "Check the kata-ci runtime of the running containerd")
        for name in after:
            task = self.task(name)
            with self.subTest(task=name):
                self.assertGreater(self.index(name), self.index("Wait for the node to be Ready"))
                self.assertIn("not ansible_check_mode", task["when"])
                self.assertIn("k3s_service_active | bool", task["when"])
        # The check reads crictl info again under the same name, so
        # kata_ci_loaded shows the restarted containerd.
        self.assertEqual(self.task(after[0])["register"], "kata_ci_crictl_info")
        check = self.task(after[3])["ansible.builtin.assert"]["that"]
        self.assertTrue(evaluate(check, **self.context()))
        self.assertFalse(evaluate(check, **self.context(live=None)))
        self.assertTrue(evaluate(check, **self.context(enabled=False, live=None)))
        self.assertFalse(evaluate(check, **self.context(enabled=False)))

    def test_generated_configuration_imports_the_dropin_directory(self):
        count = self.task("Count the drop-in imports in the generated containerd configuration")
        argv = [self.render(a, self.context()) for a in count["ansible.builtin.command"]["argv"]]
        # K3s v1.37 writes: imports = ["<data-dir>/agent/etc/containerd/config-v3.toml.d/*.toml"]
        self.assertEqual(argv, ["grep", "-c", "-F", '"/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/*.toml"',
                                "/var/lib/rancher/k3s/agent/etc/containerd/config.toml"])
        self.assertEqual(count["failed_when"], "kata_ci_imports.rc > 1")
        self.assertIn("kata_ci_on | bool", count["when"])
        check = self.task("Check that containerd imports the drop-in directory")["ansible.builtin.assert"]["that"]
        self.assertTrue(evaluate(check, kata_ci_imports={"rc": 0, "stdout": "1\n"}))
        self.assertFalse(evaluate(check, kata_ci_imports={"rc": 1, "stdout": "0\n"}))


class StandupTests(unittest.TestCase):
    def test_standup_applies_kata_ci_before_the_install(self):
        # As registries.yml: a new node reads the drop-in at its first start.
        # After the install, a new cluster has no CNI, so the Ready wait of a
        # restart would fail.
        standup = load(LAYER / "standup.yml")
        imports = [p.get("import_playbook") or p.get("ansible.builtin.import_playbook") for p in standup]
        prep = next(i for i, p in enumerate(standup) if p.get("name") == "Cluster prep")
        self.assertGreater(imports.index("kata-ci.yml"), imports.index("registries.yml"))
        self.assertLess(imports.index("kata-ci.yml"), prep)


if __name__ == "__main__":
    unittest.main()
