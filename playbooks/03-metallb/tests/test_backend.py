"""L2 MetalLB must not start either FRR backend."""

from pathlib import Path
import unittest

import yaml


class MetalLBBackendTests(unittest.TestCase):
    def test_both_frr_backends_stay_disabled(self):
        values = yaml.safe_load((Path(__file__).resolve().parents[1] / "metallb-values.yaml").read_text())
        self.assertFalse(values["speaker"]["frr"]["enabled"])
        self.assertFalse(values["frrk8s"]["enabled"])


if __name__ == "__main__":
    unittest.main()
