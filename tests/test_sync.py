import os
import json
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from mirror import DATA_PATHS, MirrorError
from mirror.__main__ import checked_source, main, probe, run_code_check, summary as write_summary
from mirror.consumer import file_urls
from mirror.validate import sha256
from support import TIME, example, fixture, modified


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.index, self.files = example()
        self.environment = {"GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": "OWNER/REPO",
                            "GITHUB_RUN_ID": "1", "GH_TOKEN": "test-only-not-a-credential"}

    def test_source_and_both_suites_overlap(self):
        ready = threading.Barrier(3, timeout=10)

        def source():
            ready.wait()
            return fixture(), TIME

        def check(command):
            ready.wait()

        with patch("mirror.__main__.fetch_source", side_effect=source), \
                patch("mirror.__main__.run_code_check", side_effect=check) as checks:
            self.assertEqual(checked_source(), (fixture(), TIME))
        self.assertEqual({call.args[0][0] for call in checks.call_args_list}, {sys.executable, "node"})

    def test_checks_have_a_deadline_and_no_publication_token(self):
        with patch.dict(os.environ, {**self.environment, "GITHUB_TOKEN": "also-test-only"}), \
                patch("mirror.__main__.subprocess.run") as run:
            run_code_check([sys.executable, "-m", "unittest"])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertEqual(run.call_args.kwargs["timeout"], 180)
        self.assertNotIn("GH_TOKEN", run.call_args.kwargs["env"])
        self.assertNotIn("GITHUB_TOKEN", run.call_args.kwargs["env"])

    def test_each_preflight_failure_prevents_publication(self):
        for failure in ("python", "javascript", "source"):
            with self.subTest(failure=failure):
                def check(command):
                    kind = "python" if command[0] == sys.executable else "javascript"
                    if kind == failure:
                        raise subprocess.CalledProcessError(1, command)

                source_error = MirrorError("Source failed") if failure == "source" else None
                with patch.dict(os.environ, self.environment), \
                        patch("mirror.__main__.run_code_check", side_effect=check), \
                        patch("mirror.__main__.fetch_source", return_value=(fixture(), TIME), side_effect=source_error), \
                        patch("mirror.__main__.build_snapshot") as build, \
                        patch("mirror.__main__.publish") as publish, \
                        patch("mirror.__main__.probe") as probes, \
                        patch("mirror.__main__.summary") as summary:
                    self.assertEqual(main(["sync"]), 1)
                    build.assert_not_called()
                    publish.assert_not_called()
                    probes.assert_not_called()
                    self.assertEqual(summary.call_args.args[0]["status"], "failed")

    def test_invalid_data_after_preflight_never_publishes(self):
        with patch.dict(os.environ, self.environment), \
                patch("mirror.__main__.checked_source", return_value=(b"truncated", TIME)), \
                patch("mirror.__main__.publish") as publish, \
                patch("mirror.__main__.probe") as probes, \
                patch("mirror.__main__.summary"):
            self.assertEqual(main(["sync"]), 1)
            publish.assert_not_called()
            probes.assert_not_called()

    def test_invalid_hostname_reports_context_and_exact_source_without_publishing(self):
        raw = modified(HostName="bad_host")
        with patch.dict(os.environ, self.environment), \
                patch("mirror.__main__.checked_source", return_value=(raw, TIME)), \
                patch("mirror.__main__.publish") as publish, \
                patch("mirror.__main__.collect_batches") as batches, \
                patch("mirror.__main__.summary") as summary:
            self.assertEqual(main(["sync"]), 1)
            publish.assert_not_called()
            batches.assert_not_called()
        report = summary.call_args.args[0]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["fetched_at"], TIME)
        self.assertEqual(report["rejected_source"], {"bytes": len(raw), "sha256": sha256(raw)})
        self.assertEqual(report["validation_error"]["field"], "HostName")
        self.assertEqual(json.loads(report["validation_error"]["value_preview"]), "bad_host")
        self.assertNotIn("publication", report["durations_seconds"])
        with patch("builtins.print") as output, patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}):
            write_summary(report)
        logged = json.loads(output.call_args.args[0])
        self.assertEqual(logged["validation_error"], report["validation_error"])
        self.assertNotIn(fixture().decode(), output.call_args.args[0])

    def test_all_six_probes_overlap_and_verify_the_same_commit(self):
        ready = threading.Barrier(6, timeout=10)
        expected = {url: self.files[path] for path in DATA_PATHS
                    for url in file_urls("OWNER/REPO", self.index["data_commit"], path)}

        def read(url, limit):
            ready.wait()
            self.assertIn(url, expected)
            self.assertEqual(limit, len(expected[url]))
            return expected[url]

        with patch("mirror.__main__.read_https", side_effect=read) as downloads:
            checks = probe("OWNER/REPO", self.index)
        self.assertEqual(downloads.call_count, 6)
        self.assertEqual(set(checks), set(DATA_PATHS))
        for results in checks.values():
            self.assertEqual(set(results), {"cdn", "raw"})
            self.assertTrue(all("verified_at" in result for result in results.values()))

    def test_probe_errors_do_not_cancel_other_downloads(self):
        def read(url, limit):
            if "cdn.jsdelivr.net" in url and url.endswith("servers.json"):
                body = self.files["data/servers.json"]
                return b"!" + body[1:]  # Same size, wrong SHA-256.
            if "raw.githubusercontent.com" in url and url.endswith("countries.json"):
                raise TimeoutError("Probe timed out")
            return next(body for path, body in self.files.items() if url.endswith(path))

        with patch("mirror.__main__.read_https", side_effect=read) as downloads:
            checks = probe("OWNER/REPO", self.index)
        self.assertEqual(downloads.call_count, 6)
        self.assertIn("error", checks["data/servers.json"]["cdn"])
        self.assertIn("verified_at", checks["data/servers.json"]["raw"])
        self.assertIn("error", checks["data/countries.json"]["raw"])
        self.assertEqual(sum("verified_at" in result for results in checks.values()
                             for result in results.values()), 4)

    def test_probes_follow_confirmed_publication_and_timings_are_reported(self):
        events = []

        def publish(*args, **kwargs):
            events.append("published")
            return {"status": "published", "index": self.index}

        def probes(*args):
            self.assertEqual(events, ["published"])
            return {path: {"cdn": {"verified_at": TIME}, "raw": {"verified_at": TIME}}
                    for path in DATA_PATHS}

        with patch.dict(os.environ, self.environment), \
                patch("mirror.__main__.checked_source", return_value=(fixture(), TIME)), \
                patch("mirror.__main__.subprocess.check_output", return_value="a" * 40), \
                patch("mirror.__main__.publish", side_effect=publish), \
                patch("mirror.__main__.probe", side_effect=probes), \
                patch("mirror.__main__.summary") as summary:
            self.assertEqual(main(["sync"]), 0)
        report = summary.call_args.args[0]
        self.assertEqual(report["status"], "published")
        self.assertEqual(set(report["durations_seconds"]),
                         {"checks_and_fetch", "build_validate", "publication", "visibility_probes", "total"})
        self.assertTrue(all(value >= 0 for value in report["durations_seconds"].values()))
