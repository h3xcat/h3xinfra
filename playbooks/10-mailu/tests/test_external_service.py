"""Render the Mailu LoadBalancer Services and check their source ranges."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1] / "charts" / "h3xinfra-mailu-pre"
PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"]
CLIENT_PORTS = {993, 995, 4190}
WORLD_PORTS = {25, 465}


def render(values, namespace="mailu"):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "values.json"
        path.write_text(json.dumps(values))
        result = subprocess.run([
            "helm", "template", "test", str(CHART), "-n", namespace, "-f", str(path),
        ], capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def services(extra_networks, policy_enabled=True, namespace="mailu"):
    docs = render({
        "fullnameOverride": "mailu-pre",
        "mailuInstanceName": "mailu-main",
        "hostname": "mail.example.test",
        "publicAddresses": {"ipv4": "192.0.2.10", "ipv6": "2001:db8::10"},
        "ipPool": {"ipAddressPools": {"addresses": {
            "ipv4": "192.0.2.10/32", "ipv6": "2001:db8::10/128"}}},
        "networkPolicy": {"enabled": policy_enabled,
                          "allowedIPv6Networks": extra_networks},
    }, namespace)
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}, docs


@unittest.skipUnless(shutil.which("helm"), "helm is not installed")
class MailuExternalService(unittest.TestCase):
    def test_client_ports_are_limited_to_private_ranges(self):
        svcs, _ = services(["2001:db8:1::/64"])
        self.assertEqual(set(svcs), {"mailu-pre-external", "mailu-pre-external-clients"})
        clients = svcs["mailu-pre-external-clients"]["spec"]
        self.assertEqual({p["port"] for p in clients["ports"]}, CLIENT_PORTS)
        self.assertEqual(clients["loadBalancerSourceRanges"], PRIVATE + ["2001:db8:1::/64"])
        # loadBalancerSourceRanges does not apply to NodePorts, so the client
        # Service must not open NodePorts.
        self.assertIs(clients["allocateLoadBalancerNodePorts"], False)

    def test_policy_allows_only_the_expected_sources_and_ports(self):
        for namespace in ("mailu", "custom-mail"):
            for extra_networks in ([], ["2001:db8:1::/64", "2001:db8:2::/64"]):
                with self.subTest(namespace=namespace, extra_networks=extra_networks):
                    svcs, docs = services(extra_networks, namespace=namespace)
                    policy, = [d for d in docs if d["kind"] == "CiliumNetworkPolicy"]
                    self.assertEqual(policy["metadata"]["namespace"], namespace)
                    client_cidrs = svcs["mailu-pre-external-clients"]["spec"]["loadBalancerSourceRanges"]
                    self.assertEqual(client_cidrs, PRIVATE + extra_networks)
                    self.assertEqual(policy["spec"], {
                        "endpointSelector": {"matchLabels": {
                            "app.kubernetes.io/component": "front",
                            "app.kubernetes.io/instance": "mailu-main",
                            "app.kubernetes.io/name": "mailu",
                        }},
                        "ingress": [
                            {"fromEntities": ["world"], "toPorts": [{"ports": [
                                {"port": str(port), "protocol": "TCP"}
                                for port in sorted(WORLD_PORTS)
                            ]}]},
                            {"fromCIDRSet": [{"cidr": cidr} for cidr in client_cidrs],
                             "toPorts": [{"ports": [
                                 {"port": str(port), "protocol": "TCP"}
                                 for port in sorted(CLIENT_PORTS)
                             ]}]},
                            {"fromEntities": ["cluster", "kube-apiserver"]},
                            {"fromEndpoints": [{"matchLabels": {
                                "k8s:io.kubernetes.pod.namespace": namespace,
                            }}]},
                        ],
                    })

    def test_mail_transfer_ports_stay_open_to_all_sources(self):
        svcs, _ = services([])
        public = svcs["mailu-pre-external"]
        self.assertEqual({p["port"] for p in public["spec"]["ports"]}, WORLD_PORTS)
        self.assertNotIn("loadBalancerSourceRanges", public["spec"])
        # The existing Service keeps its NodePorts. This change does not alter them.
        self.assertNotIn("allocateLoadBalancerNodePorts", public["spec"])
        self.assertEqual(public["metadata"]["labels"],
                         {"external-dns.alpha.kubernetes.io/publish": "true"})

    def test_services_share_the_pool_address(self):
        svcs, _ = services([])
        a, b = svcs["mailu-pre-external"], svcs["mailu-pre-external-clients"]
        for key in ("metallb.universe.tf/address-pool", "metallb.universe.tf/allow-shared-ip"):
            self.assertEqual(a["metadata"]["annotations"][key], b["metadata"]["annotations"][key])
        # MetalLB shares an address between Local-policy Services only when
        # their selectors are identical; their ports must not overlap.
        self.assertEqual(a["spec"]["selector"], b["spec"]["selector"])
        self.assertEqual(a["spec"]["externalTrafficPolicy"], "Local")
        self.assertEqual(b["spec"]["externalTrafficPolicy"], "Local")
        self.assertFalse({p["port"] for p in a["spec"]["ports"]}
                         & {p["port"] for p in b["spec"]["ports"]})
        self.assertNotIn("external-dns.kubernetes.io/hostname", b["metadata"]["annotations"])
        self.assertNotIn("labels", b["metadata"])

    def test_public_dns_uses_the_current_annotation_contract(self):
        svcs, _ = services([])
        public = svcs["mailu-pre-external"]["metadata"]
        # The pinned ExternalDNS ignores legacy alpha annotations by default.
        # Its publish label filter is a separate, unchanged selection contract.
        self.assertEqual(public["labels"],
                         {"external-dns.alpha.kubernetes.io/publish": "true"})
        self.assertEqual(public["annotations"]["external-dns.kubernetes.io/hostname"],
                         "mail.example.test")
        self.assertEqual(public["annotations"]["external-dns.kubernetes.io/target"].split(","),
                         ["192.0.2.10", "2001:db8::10"])

    def test_source_ranges_do_not_depend_on_the_cilium_policy(self):
        svcs, docs = services([], policy_enabled=False)
        self.assertFalse([d for d in docs if d["kind"] == "CiliumNetworkPolicy"])
        clients = svcs["mailu-pre-external-clients"]["spec"]
        self.assertEqual(clients["loadBalancerSourceRanges"], PRIVATE)
        self.assertIs(clients["allocateLoadBalancerNodePorts"], False)


if __name__ == "__main__":
    unittest.main()
