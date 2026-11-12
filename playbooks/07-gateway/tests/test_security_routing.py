"""Render edge security contracts locally with synthetic values."""
import base64
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


def keycloak_main(**values):
    base = {
        "fullnameOverride": "kc", "hostname": "auth.app.example.test",
        "bootstrapAdminSecretName": "admin",
        "database": {"host": "kc-pg-rw", "credentialsSecretName": "db",
                     "clusterName": "kc-pg"},
    }
    base.update(values)
    return render("10-keycloak", "h3xinfra-keycloak-main", base)


def keycloak_policy(egress=False, smtp_ports=()):
    return keycloak_main(networkPolicy={
        "enabled": True, "gateway": {"name": "shared", "namespace": "gateway"},
        "prometheusNamespace": "monitoring",
        "egress": {"enabled": egress, "smtpPorts": list(smtp_ports)},
    })


def policies(docs):
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == "CiliumNetworkPolicy"}


def ports(rule):
    return {(p["port"], p["protocol"]) for to in rule.get("toPorts", []) for p in to["ports"]}


class CoreSecurityRouting(unittest.TestCase):
    def test_cloudflare_secrets_preserve_token_bytes(self):
        token = "FAKE_cloudflare-token+/=\n"
        for component, chart in [
            ("05-certmanager", "h3xinfra-certmanager-post"),
            ("06-externaldns", "h3xinfra-externaldns-pre"),
        ]:
            with self.subTest(chart=chart):
                docs = render(component, chart, {
                    "clusterIssuer": {"acme": {"dnsZones": ["example.test"]}},
                    "cloudflareApiToken": {"token": token},
                })
                secret = next(doc for doc in docs if doc["kind"] == "Secret")
                self.assertEqual(secret["type"], "Opaque")
                self.assertEqual(base64.b64decode(secret["data"]["api-token"]), token.encode())

    def test_cloudflare_secrets_reject_missing_empty_or_null_tokens(self):
        for component, chart in [
            ("05-certmanager", "h3xinfra-certmanager-post"),
            ("06-externaldns", "h3xinfra-externaldns-pre"),
        ]:
            for token_values in [{}, {"token": ""}, {"token": None}]:
                with self.subTest(chart=chart, token_values=token_values):
                    with self.assertRaises(subprocess.CalledProcessError) as failure:
                        render(component, chart, {
                            "clusterIssuer": {"acme": {"dnsZones": ["example.test"]}},
                            "cloudflareApiToken": token_values,
                        })
                    self.assertIn("cloudflareApiToken.token is required", failure.exception.stderr)

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

    def test_gateway_replaces_client_proxy_headers(self):
        docs = render("07-gateway", "h3xinfra-gateway-pre", {})
        gateway = next(d for d in docs if d["kind"] == "Gateway")
        policy = next(d for d in docs if d["kind"] == "ClientTrafficPolicy")
        self.assertEqual(policy["spec"]["targetRefs"], [{
            "group": "gateway.networking.k8s.io", "kind": "Gateway",
            "name": gateway["metadata"]["name"]}])
        # xForwardedFor detection would make Envoy trust the client header.
        self.assertNotIn("clientIPDetection", policy["spec"])
        removed = {h.lower() for h in policy["spec"]["headers"]["earlyRequestHeaders"]["remove"]}
        self.assertLessEqual({"forwarded", "x-forwarded-for", "x-forwarded-host",
                              "x-forwarded-port", "x-forwarded-proto"}, removed)

    def test_keycloak_network_policy_is_opt_in(self):
        docs = keycloak_main()
        self.assertEqual(policies(docs), {})
        cr = next(d for d in docs if d["kind"] == "Keycloak")
        self.assertNotIn("networkPolicy", cr["spec"])
        self.assertNotIn("additionalOptions", cr["spec"])

    def test_keycloak_trusts_proxy_headers_only_from_listed_sources(self):
        docs = keycloak_main(proxyTrustedAddresses=["10.42.0.0/16", "2001:db8:cafe::/96"],
                             additionalOptions=[{"name": "log-level", "value": "info"}])
        cr = next(d for d in docs if d["kind"] == "Keycloak")
        self.assertEqual(cr["spec"]["proxy"], {"headers": "xforwarded"})
        self.assertEqual(cr["spec"]["additionalOptions"], [
            {"name": "proxy-trusted-addresses", "value": "10.42.0.0/16,2001:db8:cafe::/96"},
            {"name": "log-level", "value": "info"}])

    def test_keycloak_http_port_admits_only_the_gateway(self):
        docs = keycloak_policy()
        cr = next(d for d in docs if d["kind"] == "Keycloak")
        # The operator policy allows 8080 from every pod; Cilium adds allows.
        self.assertEqual(cr["spec"]["networkPolicy"], {"enabled": False})
        kc = policies(docs)["kc"]
        self.assertEqual(kc["spec"]["endpointSelector"]["matchLabels"], {
            "app": "keycloak", "app.kubernetes.io/instance": "kc",
            "app.kubernetes.io/managed-by": "keycloak-operator"})
        self.assertEqual(kc["spec"]["enableDefaultDeny"], {"ingress": True, "egress": False})
        self.assertNotIn("egress", kc["spec"])
        http = [r for r in kc["spec"]["ingress"] if ("8080", "TCP") in ports(r)]
        sources = [r.get("fromEndpoints") or r.get("fromEntities") for r in http]
        self.assertIn([{"matchLabels": {
            "k8s:io.kubernetes.pod.namespace": "gateway",
            "gateway.envoyproxy.io/owning-gateway-name": "shared",
            "gateway.envoyproxy.io/owning-gateway-namespace": "gateway"}}], sources)
        self.assertIn([{"matchLabels": {"app.kubernetes.io/name": "keycloak-operator"}}], sources)
        self.assertEqual(len(http), 2)
        # Kubelet probes use the management port only.
        host = [r for r in kc["spec"]["ingress"] if r.get("fromEntities") == ["host"]]
        self.assertEqual([ports(r) for r in host], [{("9000", "TCP")}])
        for rule in kc["spec"]["ingress"]:
            self.assertNotIn("fromCIDR", rule)
            self.assertTrue(rule.get("toPorts"), "every Keycloak ingress rule names its ports")

    def test_keycloak_database_admits_keycloak_operator_and_scraper(self):
        db = policies(keycloak_policy())["kc-postgres"]
        self.assertEqual(db["spec"]["endpointSelector"]["matchLabels"], {"cnpg.io/cluster": "kc-pg"})
        rules = {tuple(sorted(r.get("fromEndpoints", [{}])[0].get("matchLabels", {}).items())) or
                 tuple(r.get("fromEntities", [])): ports(r) for r in db["spec"]["ingress"]}
        self.assertEqual(rules[(("app", "keycloak"), ("app.kubernetes.io/instance", "kc"),
                                ("app.kubernetes.io/managed-by", "keycloak-operator"))],
                         {("5432", "TCP")})
        self.assertEqual(rules[(("app.kubernetes.io/name", "cloudnative-pg"),
                                ("k8s:io.kubernetes.pod.namespace", "cnpg-system"))],
                         {("8000", "TCP")})
        self.assertEqual(rules[(("app.kubernetes.io/name", "prometheus"),
                                ("k8s:io.kubernetes.pod.namespace", "monitoring"))],
                         {("9187", "TCP")})
        self.assertEqual(rules[("host",)], {("8000", "TCP")})
        self.assertEqual(rules[(("cnpg.io/cluster", "kc-pg"),)], set())
        self.assertNotIn("egress", db["spec"])

    def test_keycloak_egress_allowlist(self):
        found = policies(keycloak_policy(egress=True, smtp_ports=[465]))
        kc, db = found["kc"], found["kc-postgres"]
        for policy in (kc, db):
            self.assertEqual(policy["spec"]["enableDefaultDeny"], {"ingress": True, "egress": True})
            dns = next(r for r in policy["spec"]["egress"]
                       if r.get("toEndpoints", [{}])[0].get("matchLabels", {}).get("k8s-app") == "kube-dns")
            self.assertEqual(ports(dns), {("53", "UDP"), ("53", "TCP")})
        allowed = {tuple(sorted(r.get("toEndpoints", [{}])[0].get("matchLabels", {}).items())) or
                   tuple(r.get("toEntities", [])): ports(r) for r in kc["spec"]["egress"]}
        self.assertEqual(allowed[(("cnpg.io/cluster", "kc-pg"),)], {("5432", "TCP")})
        self.assertEqual(allowed[("world", "cluster")], {("465", "TCP")})
        self.assertEqual(len(allowed), 4)
        self.assertIn(("kube-apiserver",), {tuple(r.get("toEntities", [])) for r in db["spec"]["egress"]})

    def test_keycloak_playbook_trusts_pod_cidrs_and_checks_issuer(self):
        playbook = yaml.safe_load((ROOT / "playbooks" / "10-keycloak" / "standup.yml").read_text())
        tasks = [t for play in playbook for t in play.get("tasks", [])]
        names = [t.get("name") for t in tasks]
        values = tasks[names.index("Deploy Keycloak CR via Helm")]["kubernetes.core.helm"]["values"]
        self.assertIn("cluster_cidr.ipv4, cluster_cidr.ipv6", values["proxyTrustedAddresses"])
        self.assertIn("default(false)", values["networkPolicy"]["enabled"])
        self.assertIn("default(false)", values["networkPolicy"]["egress"]["enabled"])
        # The issuer check must reach new pods, not the pods before the
        # CR change.
        deploy = names.index("Deploy Keycloak CR via Helm")
        ready = names.index("Wait for Keycloak instance to reach Ready condition")
        rollout = names.index("Wait for the Keycloak StatefulSet rollout to finish")
        read = names.index("Read the master realm issuer from the Keycloak replicas")
        check = names.index("Verify Keycloak advertises the public issuer URL")
        self.assertLess(deploy, ready)
        self.assertLess(ready, rollout)
        self.assertLess(rollout, read)
        self.assertLess(read, check)
        self.assertIn("observedGeneration", tasks[ready]["until"])
        for field in ("observedGeneration", "updatedReplicas", "readyReplicas", "updateRevision"):
            self.assertIn(field, tasks[rollout]["until"])
        self.assertIn("loop", tasks[read])
        self.assertIn("keycloak_issuers.results", tasks[check]["ansible.builtin.assert"]["that"][0])
        self.assertIn("/realms/master", tasks[check]["ansible.builtin.assert"]["that"][0])
        # The operator NetworkPolicy allows 8080 from every pod.
        netpol = tasks[names.index("Wait for the Keycloak Operator to remove its NetworkPolicy")]
        self.assertEqual(netpol["kubernetes.core.k8s_info"]["name"],
                         "{{ keycloak.release_name_prefix }}-main-network-policy")
        self.assertIn("keycloak.network_policy.enabled", netpol["when"])

    def test_gateway_playbook_exposes_client_traffic_policy_switch(self):
        playbook = yaml.safe_load((ROOT / "playbooks" / "07-gateway" / "standup.yml").read_text())
        tasks = [t for play in playbook for t in play.get("tasks", [])]
        names = [t.get("name") for t in tasks]
        values = tasks[names.index("Deploy Gateway pre-configuration via Helm")]["kubernetes.core.helm"]["values"]
        self.assertEqual(values["gateway"]["clientTrafficPolicy"]["enabled"],
                         "{{ gateway.client_traffic_policy.enabled | default(true) }}")
        accepted = tasks[names.index("Wait for the ClientTrafficPolicy to be accepted")]
        self.assertIn("gateway.client_traffic_policy.enabled", accepted["when"])


if __name__ == "__main__":
    unittest.main()
