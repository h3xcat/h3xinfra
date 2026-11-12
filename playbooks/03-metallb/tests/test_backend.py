"""Keep the existing FRR-K8s backend instead of enabling legacy speaker FRR."""

from pathlib import Path
import unittest

import yaml


class MetalLBBackendTests(unittest.TestCase):
    def test_legacy_frr_stays_disabled_and_frrk8s_keeps_chart_defaults(self):
        values = yaml.safe_load((Path(__file__).resolve().parents[1] / "metallb-values.yaml").read_text())
        self.assertFalse(values["speaker"]["frr"]["enabled"])
        self.assertNotIn("frrk8s", values)


if __name__ == "__main__":
    unittest.main()
