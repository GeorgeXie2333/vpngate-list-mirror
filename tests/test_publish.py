import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mirror import DATA_PATHS, MirrorError
from mirror.publish import Git, git_environment, publish
from mirror.snapshot import build_snapshot, parse_json, verify_files
from support import RUN_URL, TIME, fixture
from mirror.pool import build_pool, verify_pool
from test_pool import source, at, report as batch_report


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = tempfile.TemporaryDirectory(prefix="vpngate-test-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote, self.seed = root / "remote.git", root / "seed"
        self.seed.mkdir()
        self.env = git_environment()
        self.git = Git(self.seed, self.env)
        self.git.run("init", "--bare", str(self.remote))
        self.git.run("init", "-b", "main")
        (self.seed / "README.md").write_text("Initial documentation\n")
        (self.seed / "code.py").write_text("# initial code\n")
        self.git.run("add", ".")
        self.git.run("commit", "-m", "Initial code")
        self.git.run("remote", "add", "origin", str(self.remote))
        self.git.run("push", "-u", "origin", "main")
        self.base = self.git.text("rev-parse", "HEAD")
        self.remote_git = Git(self.remote, self.env)
        self.snapshot = build_snapshot(fixture())

    def publish(self, **kwargs):
        arguments = dict(remote=str(self.remote), source_revision=self.base, fetched_at=TIME,
                         generated_at=TIME, run_url=RUN_URL)
        arguments.update(kwargs)
        return publish(self.snapshot, **arguments)

    def head(self):
        return self.remote_git.text("rev-parse", "main")

    def remote_index(self):
        return parse_json(self.remote_git.blob("main", "latest.json"))

    def user_commit(self, path="README.md", contents="Concurrent user edit\n"):
        (self.seed / path).write_text(contents, newline="\n")
        self.git.run("add", "--", path)
        self.git.run("commit", "-m", "User edit")
        self.git.run("push", "origin", "main")

    def test_two_commit_publication_and_pinned_data(self):
        result = self.publish()
        self.assertEqual(result["status"], "published")
        index = self.remote_index()
        self.assertEqual(self.remote_git.text("rev-parse", "main^"), index["data_commit"])
        self.assertEqual(self.remote_git.text("rev-parse", "main^^"), self.base)
        self.assertIsNone(self.remote_git.blob(index["data_commit"], "latest.json"))
        files = {path: self.remote_git.blob(index["data_commit"], path) for path in DATA_PATHS}
        verify_files(index, files)

    def test_unchanged_response_only_updates_index(self):
        self.publish()
        before, head = self.remote_index(), self.head()
        blobs = {path: self.remote_git.text("rev-parse", f"main:{path}") for path in DATA_PATHS}
        result = self.publish(source_revision=head, fetched_at="2026-09-01T01:00:00.000Z", generated_at="2026-09-01T01:00:00.000Z")
        after = self.remote_index()
        self.assertFalse(result["data_changed"])
        self.assertEqual(after["data_commit"], before["data_commit"])
        self.assertEqual(after["generated_at"], before["generated_at"])
        self.assertNotEqual(after["fetched_at"], before["fetched_at"])
        self.assertEqual(self.remote_git.text("rev-parse", "main^"), head)
        self.assertEqual(blobs, {path: self.remote_git.text("rev-parse", f"main:{path}") for path in DATA_PATHS})

    def test_failure_before_index_leaves_remote_untouched(self):
        with patch("mirror.publish.make_index", side_effect=MirrorError("Index validation failed")):
            with self.assertRaises(MirrorError):
                self.publish()
        self.assertEqual(self.head(), self.base)

    def test_conflict_rebuilds_both_commits_without_losing_user_edit(self):
        attempts = []
        def race(attempt, data, index):
            attempts.append(data)
            if attempt == 1:
                self.user_commit()
        result = self.publish(before_push=race)
        self.assertEqual(result["push_attempts"], 2)
        self.assertNotEqual(attempts[0], attempts[1])
        self.assertEqual(self.remote_index()["data_commit"], attempts[1])
        self.assertEqual(self.remote_git.blob("main", "README.md"), b"Concurrent user edit\n")

    def test_code_race_explicitly_fails(self):
        def race(attempt, data, index):
            self.user_commit("code.py", "# new user implementation\n")
        with self.assertRaisesRegex(MirrorError, "Concurrent code/data"):
            self.publish(before_push=race)
        self.assertIsNone(self.remote_git.blob("main", "latest.json"))
        self.assertEqual(self.remote_git.blob("main", "code.py"), b"# new user implementation\n")

    def test_race_retries_are_bounded(self):
        def race(attempt, data, index):
            self.user_commit(contents=f"User change {attempt}\n")
        with self.assertRaisesRegex(MirrorError, "3 attempts"):
            self.publish(before_push=race)
        self.assertEqual(self.remote_git.blob("main", "README.md"), b"User change 3\n")
        self.assertIsNone(self.remote_git.blob("main", "latest.json"))

    def test_older_fetch_cannot_overwrite_new_success(self):
        self.publish(fetched_at="2026-09-01T02:00:00.000Z", generated_at="2026-09-01T02:00:00.000Z")
        head = self.head()
        self.assertEqual(self.publish(source_revision=head)["status"], "superseded")
        self.assertEqual(self.head(), head)

    def test_lost_push_ack_is_recognized(self):
        original = Git.run
        def lost_ack(git, *args, **kwargs):
            result = original(git, *args, **kwargs)
            if args[0] == "push" and result.returncode == 0:
                return subprocess.CompletedProcess(result.args, 1, b"", b"ACK lost")
            return result
        with patch.object(Git, "run", lost_ack):
            result = self.publish()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["index_commit"], self.head())

    def test_existing_success_survives_invalid_candidate(self):
        self.publish()
        head, previous = self.head(), self.remote_git.blob("main", "latest.json")
        self.snapshot.files["data/servers.json"] += b"corrupt"
        with self.assertRaises(MirrorError):
            self.publish(source_revision=head, fetched_at="2026-09-01T03:00:00.000Z", generated_at="2026-09-01T03:00:00.000Z")
        self.assertEqual(self.head(), head)
        self.assertEqual(self.remote_git.blob("main", "latest.json"), previous)

    def test_pool_and_mirror_indexes_pin_the_same_initial_transaction(self):
        result = self.publish(pool_enabled=True)
        pool_index = result["pool_index"]
        self.assertEqual(pool_index["data_commit"], result["data_commit"])
        self.assertEqual(self.remote_git.text("rev-parse", "main^"), pool_index["data_commit"])
        self.assertIsNone(self.remote_git.blob(pool_index["data_commit"], "pool/latest.json"))
        paths = self.remote_git.text("ls-tree", "-r", "--name-only", pool_index["data_commit"], "pool").splitlines()
        verify_pool(pool_index, {path: self.remote_git.blob(pool_index["data_commit"], path) for path in paths})

    def test_new_observations_reuse_mirror_data_and_config_blobs(self):
        first = self.publish(pool_enabled=True)
        path = first["pool_sample_config"]["path"]
        blob = self.remote_git.text("rev-parse", f"main:{path}")
        second = self.publish(pool_enabled=True, source_revision=self.head(), fetched_at=at(1), generated_at=at(1))
        self.assertEqual(first["data_commit"], second["data_commit"])
        self.assertNotEqual(first["pool_index"]["data_commit"], second["pool_index"]["data_commit"])
        self.assertEqual(blob, self.remote_git.text("rev-parse", f"main:{path}"))
        self.assertEqual(second["pool"]["reader"]["status"], "not_configured")

    def test_pool_failure_before_push_and_retry_do_not_acknowledge_early(self):
        self.snapshot = source()
        self.publish(pool_enabled=True)
        before = self.head()
        first_pool = build_pool(self.snapshot, at())
        batch = batch_report(first_pool, 24)
        self.snapshot = source("1.1.1.1")
        reader = lambda processed: ([batch], {"status": "read"})
        def stop(*args):
            raise MirrorError("Push failed before acceptance")
        with self.assertRaises(MirrorError):
            self.publish(pool_enabled=True, source_revision=before, fetched_at=at(24), generated_at=at(24), result_reader=reader, before_push=stop)
        self.assertEqual(self.head(), before)
        state = parse_json(self.remote_git.blob("main", "pool/state.json"))
        self.assertNotIn(batch["batch_id"], state["processed_batches"])
        self.publish(pool_enabled=True, source_revision=before, fetched_at=at(24), generated_at=at(24), result_reader=reader)
        state = parse_json(self.remote_git.blob("main", "pool/state.json"))
        self.assertIn(batch["batch_id"], state["processed_batches"])

    def test_pool_documentation_race_rebuilds_indexes_and_lost_ack_is_safe(self):
        original = Git.run
        def lost_ack(git, *args, **kwargs):
            result = original(git, *args, **kwargs)
            if args[0] == "push" and result.returncode == 0:
                return subprocess.CompletedProcess(result.args, 1, b"", b"ACK lost")
            return result
        def race(attempt, data, index):
            if attempt == 1:
                self.user_commit()
        # The injected user commit itself must receive a normal ACK.
        result = self.publish(pool_enabled=True, before_push=race)
        self.assertEqual(result["push_attempts"], 2)
        self.assertEqual(result["pool_index"]["data_commit"], result["data_commit"])
        with patch.object(Git, "run", lost_ack):
            accepted = self.publish(pool_enabled=True, source_revision=self.head(), fetched_at=at(1), generated_at=at(1))
        self.assertEqual(accepted["status"], "published")
        self.assertEqual(parse_json(self.remote_git.blob("main", "pool/latest.json")), accepted["pool_index"])

    def test_bad_pool_preserves_both_previous_indexes(self):
        first = self.publish(pool_enabled=True)
        before = self.head()
        with patch("mirror.publish.verify_pool", side_effect=MirrorError("Corrupt pool")):
            with self.assertRaises(MirrorError):
                self.publish(pool_enabled=True, source_revision=before, fetched_at=at(1), generated_at=at(1))
        self.assertEqual(self.head(), before)
        self.assertEqual(self.remote_index(), first["index"])
        self.assertEqual(parse_json(self.remote_git.blob("main", "pool/latest.json")), first["pool_index"])
