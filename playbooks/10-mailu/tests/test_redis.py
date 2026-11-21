"""Render the Redis of the Mailu pre-chart and check its access, security and storage."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1] / "charts" / "h3xinfra-mailu-pre"
TAG = "8.2.11@sha256:" + "0" * 64


def render(namespace="mailu", **values):
    values = {"fullnameOverride": "mailu-pre", "mailuInstanceName": "mailu-main",
              "redis": {"image": {"tag": TAG}}, **values}
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "values.json"
        path.write_text(json.dumps(values))
        result = subprocess.run(["helm", "template", "test", str(CHART), "-n", namespace, "-f", str(path)],
                                capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(result.stderr)
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(result.stdout) if d}


def config_lines(conf):
    return [line for line in conf.splitlines() if line.strip() and not line.startswith("#")]


@unittest.skipUnless(shutil.which("helm"), "helm is not installed")
class MailuRedis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = render()
        cls.sts = cls.docs[("StatefulSet", "mailu-pre-redis")]
        cls.pod = cls.sts["spec"]["template"]
        cls.container, = cls.pod["spec"]["containers"]

    def test_image_tag_is_required(self):
        with self.assertRaisesRegex(AssertionError, "redis.image.tag is required"):
            render(redis={"image": {"tag": ""}})

    def test_redis_server_runs_without_the_image_entrypoint(self):
        # The entrypoint would also load the search, JSON, time series and
        # probabilistic modules of the image.
        self.assertEqual(self.container["image"], "docker.io/library/redis:" + TAG)
        self.assertEqual(self.container["command"], ["redis-server", "/etc/redis/redis.conf"])
        self.assertNotIn("args", self.container)

    def test_pod_runs_as_the_redis_user_with_a_read_only_root(self):
        spec = self.pod["spec"]
        self.assertIs(spec["automountServiceAccountToken"], False)
        self.assertEqual(spec["securityContext"], {
            "runAsNonRoot": True, "runAsUser": 999, "runAsGroup": 999, "fsGroup": 999,
            "fsGroupChangePolicy": "OnRootMismatch", "seccompProfile": {"type": "RuntimeDefault"},
        })
        self.assertEqual(self.container["securityContext"], {
            "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        })
        # The data PVC is the only writable mount.
        mounts = {m["mountPath"]: m for m in self.container["volumeMounts"]}
        self.assertEqual(set(mounts), {"/data", "/etc/redis"})
        self.assertIs(mounts["/etc/redis"]["readOnly"], True)
        for bound in ("requests", "limits"):
            self.assertEqual(set(self.container["resources"][bound]), {"cpu", "memory"})

    def test_persistence_matches_the_bundled_redis(self):
        conf = self.docs[("ConfigMap", "mailu-pre-redis")]["data"]["redis.conf"]
        self.assertEqual(config_lines(conf), [
            "port 6379", "protected-mode no", "dir /data",
            "appendonly yes", "appendfsync everysec", 'save ""',
            'rename-command FLUSHDB ""', 'rename-command FLUSHALL ""',
        ])
        # A config change restarts Redis.
        self.assertEqual(self.pod["metadata"]["annotations"]["checksum/config"],
                         hashlib.sha256((CHART / "files" / "redis.conf").read_bytes()).hexdigest())
        claim, = self.sts["spec"]["volumeClaimTemplates"]
        self.assertEqual(claim["metadata"]["name"], "data")
        self.assertEqual(claim["spec"]["storageClassName"], "longhorn")
        self.assertEqual(claim["spec"]["accessModes"], ["ReadWriteOnce"])
        self.assertEqual(self.sts["spec"]["persistentVolumeClaimRetentionPolicy"],
                         {"whenDeleted": "Retain", "whenScaled": "Retain"})
        self.assertEqual(self.sts["spec"]["replicas"], 1)

    def test_service_selects_only_the_redis_pod(self):
        svc = self.docs[("Service", "mailu-pre-redis")]["spec"]
        self.assertEqual(svc["type"], "ClusterIP")
        self.assertEqual(svc["ports"], [{"name": "redis", "port": 6379, "targetPort": "redis", "protocol": "TCP"}])
        self.assertEqual(svc["selector"], self.pod["metadata"]["labels"])
        self.assertEqual(svc["selector"]["app.kubernetes.io/component"], "redis")
        self.assertEqual(self.container["ports"], [{"name": "redis", "containerPort": 6379, "protocol": "TCP"}])

    def test_only_pods_of_the_release_namespace_reach_redis(self):
        # Redis has no password. The policy does not depend on the Cilium
        # policy switch, and the empty podSelector without a namespaceSelector
        # admits the policy's own namespace only.
        for namespace in ("mailu", "custom-mail"):
            for enabled in (True, False):
                with self.subTest(namespace=namespace, network_policy=enabled):
                    docs = render(namespace, networkPolicy={"enabled": enabled})
                    policies = [d for d in docs.values() if d["kind"] == "NetworkPolicy"]
                    self.assertEqual(len(policies), 1)
                    policy = policies[0]
                    self.assertEqual(policy["metadata"]["namespace"], namespace)
                    self.assertEqual(policy["spec"], {
                        "podSelector": {"matchLabels": self.pod["metadata"]["labels"]},
                        "policyTypes": ["Ingress"],
                        "ingress": [{"from": [{"podSelector": {}}],
                                     "ports": [{"port": 6379, "protocol": "TCP"}]}],
                    })


if __name__ == "__main__":
    unittest.main()
