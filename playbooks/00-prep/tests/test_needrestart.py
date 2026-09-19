"""Evaluate the managed Perl config without invoking needrestart or services."""

import json
from pathlib import Path
import subprocess
import unittest


CONFIG = Path(__file__).resolve().parents[1] / "files/50-h3xinfra-k3s.conf"

# Follow needrestart's first-match selection while keeping distribution defaults.
SELECT_SERVICES = r"""
use strict;
use warnings;
our %nrconf = (override_rc => {
    qr(^dbus) => 0,
    qr(^example-custom\.service$) => 1,
});
my $config = shift @ARGV;
my $result = do $config;
die $@ if $@;
die "Cannot load $config: $!" unless defined $result;
my %decisions;
for my $unit (@ARGV) {
    my $restart = 1;
    for my $re (sort keys %{$nrconf{override_rc}}) {
        next unless $unit =~ /$re/;
        $restart = $nrconf{override_rc}->{$re};
        last;
    }
    $decisions{$unit} = $restart;
}
print encode_json(\%decisions);
"""


class NeedrestartConfigTests(unittest.TestCase):
    def decisions(self, *units):
        result = subprocess.run(
            ["perl", "-MJSON::PP=encode_json", "-e", SELECT_SERVICES, str(CONFIG), *units],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return json.loads(result.stdout)

    def test_k3s_and_container_units_are_deferred(self):
        units = (
            "k3s.service",
            "k3s-agent.service",
            "cri-containerd-abc123.scope",
            "cri-containerd-abc123.service",
            "cri-containerd-abc123.scope.service",
        )
        self.assertEqual(self.decisions(*units), dict.fromkeys(units, 0))

    def test_unrelated_services_remain_eligible(self):
        units = (
            "ssh.service",
            "containerd.service",
            "h3xinfra-kubelet-watchdog.service",
            "k3s-helper.service",
            "k3s-agentXservice",
            "unrelated-k3s.service",
        )
        self.assertEqual(self.decisions(*units), dict.fromkeys(units, 1))

    def test_existing_distribution_overrides_are_retained(self):
        self.assertEqual(
            self.decisions("dbus.service", "example-custom.service"),
            {"dbus.service": 0, "example-custom.service": 1},
        )


if __name__ == "__main__":
    unittest.main()
