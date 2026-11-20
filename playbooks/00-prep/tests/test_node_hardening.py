"""Static checks for the SSH, rpcbind, pstore and eth0 settings of 00-prep."""

from pathlib import Path
import shlex
import unittest

import yaml


PREP = Path(__file__).resolve().parents[1]


def config_lines(content):
    return [line for line in content.splitlines() if line and not line.startswith("#")]


class NodeHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tasks = yaml.safe_load((PREP / "standup.yml").read_text())[0]["tasks"]
        self.names = [task["name"] for task in self.tasks]

    def task(self, name):
        return self.tasks[self.names.index(name)]

    def test_sshd_allows_only_key_logins(self):
        task = self.task("Allow only key-based SSH logins")
        copy = task["ansible.builtin.copy"]
        directory, name = copy["dest"].rsplit("/", 1)
        self.assertEqual(directory, "/etc/ssh/sshd_config.d")
        # sshd keeps the first value it reads; 50-cloud-init.conf may enable
        # password logins on installer images.
        self.assertLess(name, "50-cloud-init.conf")
        self.assertEqual(config_lines(copy["content"]),
                         ["PasswordAuthentication no", "KbdInteractiveAuthentication no"])
        # The copy module formats validate with % and runs it without a shell.
        argv = shlex.split(copy["validate"] % "/tmp/ansible-source")
        self.assertEqual(argv[:2], ["/bin/sh", "-c"])
        self.assertEqual(argv[-2:], ["sh", "/tmp/ansible-source"])
        self.assertIn('printf "Include %s\\nInclude /etc/ssh/sshd_config\\n" "$1"', argv[2])
        self.assertIn("| /usr/sbin/sshd -t -f /dev/stdin", argv[2])

        reload = self.tasks[self.names.index(task["name"]) + 1]
        self.assertEqual(reload["ansible.builtin.service"], {"name": "ssh", "state": "reloaded"})
        self.assertEqual(reload["when"], f"{task['register']}.changed")

    def test_rpcbind_socket_stops_first(self):
        task = self.task("Disable rpcbind")
        self.assertEqual(task["loop"], ["rpcbind.socket", "rpcbind.service"])
        self.assertEqual(task["ansible.builtin.systemd_service"],
                         {"name": "{{ item }}", "state": "stopped", "enabled": False})
        self.assertNotIn("failed_when", task)

    def test_pstore_records_are_never_aged_out(self):
        copy = self.task("Keep archived kernel panic records")["ansible.builtin.copy"]
        # Same name as /usr/lib/tmpfiles.d/systemd-pstore.conf, so it masks it.
        self.assertEqual(copy["dest"], "/etc/tmpfiles.d/systemd-pstore.conf")
        self.assertEqual(config_lines(copy["content"]), ["d /var/lib/systemd/pstore 0755 root root -"])

    def test_eth0_keeps_addresses_through_carrier_loss(self):
        check = self.task("Check for the netplan eth0 network unit")
        self.assertEqual(check["ansible.builtin.stat"]["path"], "/run/systemd/network/10-netplan-eth0.network")
        dropin = self.task("Keep eth0 addresses through carrier loss")
        ini = dropin["community.general.ini_file"]
        # ini_file also creates the drop-in directory (and stays usable in
        # check mode, unlike a copy into a directory that does not exist yet).
        self.assertEqual(ini["path"], "/etc/systemd/network/10-netplan-eth0.network.d/ignore-carrier-loss.conf")
        self.assertEqual((ini["section"], ini["option"], ini["value"]), ("Network", "IgnoreCarrierLoss", "yes"))
        self.assertIs(ini["no_extra_spaces"], True)
        self.assertEqual(dropin["when"], f"{check['register']}.stat.exists")

    def test_play_does_not_reload_the_network(self):
        # The eth0 drop-in waits for the next reboot; a networkd reload or a
        # netplan apply on every node at once can drop the whole cluster.
        for task in self.tasks:
            with self.subTest(task=task["name"]):
                text = yaml.safe_dump(task)
                self.assertNotIn("systemd-networkd", text)
                self.assertNotIn("networkctl", text)
                self.assertNotIn("netplan apply", text)


if __name__ == "__main__":
    unittest.main()
