"""Keep Cilium and Envoy DaemonSet updates serial within each controller."""

from pathlib import Path
import unittest

import yaml


class CiliumRolloutTests(unittest.TestCase):
    def test_agent_and_envoy_roll_one_node_at_a_time(self):
        values = yaml.safe_load((Path(__file__).resolve().parents[1] / "cilium-values.yaml").read_text())
        for component in (values, values["envoy"]):
            self.assertEqual(component["updateStrategy"], {
                "type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 1},
            })


if __name__ == "__main__":
    unittest.main()
