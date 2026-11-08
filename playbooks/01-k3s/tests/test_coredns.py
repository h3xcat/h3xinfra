"""Check the CoreDNS replica and disruption settings without a cluster."""

from pathlib import Path
import unittest

import yaml


LAYER = Path(__file__).resolve().parents[1]
PLAYBOOK = LAYER / "coredns.yml"
STACK = LAYER.parent / "stack-standup.yml"
# Pod labels of the K3s packaged manifest (manifests/coredns.yaml).
K3S_POD_LABELS = {"k8s-app": "kube-dns"}


def tasks():
    plays = yaml.safe_load(PLAYBOOK.read_text())
    play = next(p for p in plays if "tasks" in p)
    return play, {t["name"]: t for t in play["tasks"]}


class CoreDNSLayerTests(unittest.TestCase):
    def test_default_is_two_replicas_of_the_k3s_deployment(self):
        play, by_name = tasks()
        self.assertEqual(play["vars"]["coredns_replicas"], "{{ coredns_settings.replicas | default(2) }}")
        scale = by_name["Scale the K3s CoreDNS Deployment"]["kubernetes.core.k8s_scale"]
        self.assertEqual((scale["kind"], scale["name"], scale["namespace"]),
                         ("Deployment", "coredns", "kube-system"))
        self.assertTrue(scale["wait"])

    def test_budget_selects_coredns_and_cannot_hold_a_drain_on_a_dead_pod(self):
        _, by_name = tasks()
        pdb = by_name["Define the CoreDNS PodDisruptionBudget"]["kubernetes.core.k8s"]["definition"]
        self.assertEqual(pdb["kind"], "PodDisruptionBudget")
        self.assertEqual(pdb["metadata"]["namespace"], "kube-system")
        self.assertEqual(pdb["spec"]["selector"], {"matchLabels": K3S_POD_LABELS})
        self.assertEqual(pdb["spec"]["maxUnavailable"], 1)
        self.assertNotIn("minAvailable", pdb["spec"])
        self.assertEqual(pdb["spec"]["unhealthyPodEvictionPolicy"], "AlwaysAllow")

    def test_budget_exists_before_the_scale_up(self):
        _, by_name = tasks()
        names = list(by_name)
        self.assertLess(names.index("Define the CoreDNS PodDisruptionBudget"),
                        names.index("Scale the K3s CoreDNS Deployment"))

    def test_stack_runs_the_layer_after_the_cni(self):
        imports = [p.get("import_playbook") for p in yaml.safe_load(STACK.read_text())]
        self.assertIn("01-k3s/coredns.yml", imports)
        self.assertGreater(imports.index("01-k3s/coredns.yml"), imports.index("02-cilium/standup.yml"))
        k3s = [p.get("import_playbook") for p in yaml.safe_load((LAYER / "standup.yml").read_text())]
        self.assertNotIn("coredns.yml", k3s)


if __name__ == "__main__":
    unittest.main()
