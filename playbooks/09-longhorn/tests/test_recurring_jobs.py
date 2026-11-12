"""Scheduled backup jobs are selected by the job list, including during upgrades."""

import json
import os
from pathlib import Path
import subprocess
import unittest

import yaml


CHART = Path(__file__).resolve().parents[1] / "charts/h3xinfra-longhorn-post"
JOB = {
    "name": "weekly-backup",
    "task": "backup",
    "cron": "0 4 * * 0",
    "retain": 8,
    "concurrency": 1,
    "groups": ["default"],
}


def recurring_jobs(values):
    result = subprocess.run(
        [os.environ.get("HELM", "helm"), "template", "test-longhorn", str(CHART),
         "-n", "longhorn-system", "-f", "-"],
        input=json.dumps({"recurringJobs": values}), capture_output=True, text=True,
        check=True, timeout=30,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout)
            if isinstance(doc, dict) and doc.get("kind") == "RecurringJob"]


class RecurringJobTests(unittest.TestCase):
    def test_job_survives_removal_of_legacy_enable_flag(self):
        expected = recurring_jobs({"enabled": True, "jobs": [JOB]})
        self.assertEqual(len(expected), 1)
        self.assertEqual(expected[0]["metadata"]["name"], JOB["name"])
        self.assertEqual(expected[0]["metadata"]["namespace"], "longhorn-system")
        self.assertEqual(expected[0]["spec"], {k: v for k, v in JOB.items() if k != "name"})
        self.assertEqual(recurring_jobs({"jobs": [JOB]}), expected)
        self.assertEqual(recurring_jobs({"enabled": False, "jobs": [JOB]}), expected)

    def test_empty_list_has_no_scheduled_jobs(self):
        self.assertEqual(recurring_jobs({"jobs": []}), [])

    def test_incomplete_jobs_fail_rendering(self):
        for field in ("name", "task", "cron", "retain"):
            with self.subTest(field=field), self.assertRaises(subprocess.CalledProcessError):
                recurring_jobs({"jobs": [{k: v for k, v in JOB.items() if k != field}]})


if __name__ == "__main__":
    unittest.main()
