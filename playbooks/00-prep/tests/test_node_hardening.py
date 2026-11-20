"""Static checks for the SSH, rpcbind, pstore and eth0 settings of 00-prep."""

from pathlib import Path
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
        self.assertIn("sshd -t", copy["validate"])

        reload = self.tasks[self.names.index(task["name"]) + 1]
        self.assertEqual(reload["ansible.builtin.service"]["state"], "reloaded")
        self.assertEqual(reload["when"], f"{task['register']}.changed")

    def test_rpcbind_socket_stops_first(self):
        # A running socket would start the service again.
        self.assertEqual(self.task("Disable rpcbind")["loop"], ["rpcbind.socket", "rpcbind.service"])

    def test_pstore_records_are_never_aged_out(self):
        copy = self.task("Keep archived kernel panic records")["ansible.builtin.copy"]
        # Same name as /usr/lib/tmpfiles.d/systemd-pstore.conf, so it masks it.
        self.assertEqual(copy["dest"], "/etc/tmpfiles.d/systemd-pstore.conf")
        self.assertEqual(config_lines(copy["content"]), ["d /var/lib/systemd/pstore 0755 root root -"])

    def test_eth0_drop_in_only_where_the_netplan_unit_exists(self):
        check = self.task("Check for the netplan eth0 network unit")
        self.assertEqual(check["ansible.builtin.stat"]["path"], "/run/systemd/network/10-netplan-eth0.network")
        dropin = self.task("Keep eth0 addresses through carrier loss")
        ini = dropin["community.general.ini_file"]
        self.assertEqual(ini["path"], "/etc/systemd/network/10-netplan-eth0.network.d/ignore-carrier-loss.conf")
        self.assertEqual((ini["section"], ini["option"], ini["value"]), ("Network", "IgnoreCarrierLoss", "yes"))
        self.assertEqual(dropin["when"], f"{check['register']}.stat.exists")


if __name__ == "__main__":
    unittest.main()
