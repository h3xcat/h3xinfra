"""Render edge security contracts locally with synthetic values."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[3]


def render(component, chart, values):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "values.json"
        path.write_text(json.dumps(values))
        result = subprocess.run([
            "helm", "template", "test", str(ROOT / "playbooks" / component / "charts" / chart),
            "-n", "test-ns", "-f", str(path),
        ], capture_output=True, text=True, check=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def keycloak(allowlist):
    return render("10-keycloak", "h3xinfra-keycloak-pre", {
        "bootstrapSecret": {"enabled": False}, "postgres": {"enabled": False},
        "ingress": {"enabled": True, "host": "auth.app.example.test",
                    "gateway": {"name": "shared", "namespace": "gateway"},
                    "serviceName": "keycloak", "servicePort": 8080,
                    "trustedSetupIPs": allowlist},
    })


class CoreSecurityRouting(unittest.TestCase):
    def test_keycloak_restriction_targets_only_admin_rule(self):
        cidrs = ["192.0.2.0/24", "2001:db8::/64"]
        docs = keycloak(cidrs)
        route = next(d for d in docs if d["kind"] == "HTTPRoute")
        policy = next(d for d in docs if d["kind"] == "SecurityPolicy")
        self.assertEqual(route["spec"]["parentRefs"][0]["port"], 443)
        self.assertNotIn("sectionName", route["spec"]["parentRefs"][0])
        self.assertEqual({r["name"] for r in route["spec"]["rules"]}, {"public", "admin"})
        public = next(r for r in route["spec"]["rules"] if r["name"] == "public")
        self.assertIn("/realms/", {m["path"]["value"] for m in public["matches"]})
        self.assertEqual(policy["spec"]["targetRefs"], [{
            "group": "gateway.networking.k8s.io", "kind": "HTTPRoute",
            "name": route["metadata"]["name"], "sectionName": "admin"}])
        authorization = policy["spec"]["authorization"]
        self.assertEqual(authorization["defaultAction"], "Deny")
        self.assertEqual(authorization["rules"], [{"action": "Allow", "principal": {"clientCIDRs": cidrs}}])

    def test_keycloak_attaches_to_the_listener_selected_by_app_sni(self):
        gateway = next(d for d in render("07-gateway", "h3xinfra-gateway-pre", {
            "gateway": {"listeners": {
                "https": {"hostname": "*.example.test"},
                "httpsApp": {"hostname": "*.app.example.test"},
                "httpsApex": {"hostname": "example.test"},
            }},
        }) if d["kind"] == "Gateway")
        route = next(d for d in keycloak([]) if d["kind"] == "HTTPRoute")
        host = route["spec"]["hostnames"][0]
        candidates = [l for l in gateway["spec"]["listeners"] if l["protocol"] == "HTTPS"
                      and (host == l["hostname"] or
                           (l["hostname"].startswith("*.") and host.endswith(l["hostname"][1:])))]
        selected = max(candidates, key=lambda l: len(l["hostname"]))
        self.assertEqual(selected["name"], "https-app")
        parents = route["spec"]["parentRefs"]
        self.assertTrue(any(p.get("sectionName", selected["name"]) == selected["name"]
                            and p.get("port", selected["port"]) == selected["port"]
                            for p in parents), "route must attach where TLS SNI sends its hostname")
        for parent in parents:
            attached = [l for l in gateway["spec"]["listeners"]
                        if parent.get("port", l["port"]) == l["port"]
                        and parent.get("sectionName", l["name"]) == l["name"]]
            self.assertTrue(attached)
            self.assertEqual({l["protocol"] for l in attached}, {"HTTPS"})

    def test_empty_keycloak_allowlist_denies_admin(self):
        policy = next(d for d in keycloak([]) if d["kind"] == "SecurityPolicy")
        self.assertEqual(policy["spec"]["authorization"], {"defaultAction": "Deny"})

    def test_longhorn_attaches_only_to_https(self):
        docs = render("09-longhorn", "h3xinfra-longhorn-post", {
            "longhornIngress": {"host": "longhorn.example.test", "trustedIPs": ["192.0.2.0/24"],
                                "gateway": {"name": "shared", "namespace": "gateway"}},
        })
        route = next(d for d in docs if d["kind"] == "HTTPRoute")
        self.assertEqual(route["spec"]["parentRefs"][0]["port"], 443)
        self.assertNotIn("sectionName", route["spec"]["parentRefs"][0])

    def test_mail_web_and_public_autoconfig_use_https(self):
        docs = render("10-mailu", "h3xinfra-mailu-pre", {
            "certificate": {"dnsNames": ["mail.example.test"]},
            "gatewayRoute": {"enabled": True, "gateway": {"name": "shared", "namespace": "gateway"},
                             "oidc": {"enabled": True, "clientSecret": "synthetic", "secretRef": {"name": "oidc"}},
                             "publicPaths": ["/.well-known/", "/autoconfig"]},
        })
        routes = [d for d in docs if d["kind"] == "HTTPRoute"]
        self.assertEqual(len(routes), 2)
        for route in routes:
            self.assertEqual(route["spec"]["parentRefs"][0]["port"], 443)
            self.assertNotIn("sectionName", route["spec"]["parentRefs"][0])

    def test_global_redirect_stays_on_http(self):
        docs = render("07-gateway", "h3xinfra-gateway-pre", {})
        redirect = next(d for d in docs if d["kind"] == "HTTPRoute")
        self.assertEqual({p["sectionName"] for p in redirect["spec"]["parentRefs"]}, {"http", "http-apex"})
        self.assertEqual(redirect["spec"]["rules"][0]["filters"][0]["requestRedirect"]["scheme"], "https")


if __name__ == "__main__":
    unittest.main()
