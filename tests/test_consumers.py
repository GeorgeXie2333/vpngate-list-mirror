import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mirror import DATA_PATHS, MirrorError
from mirror.consumer import install_cache, load_snapshot, read_cache
from mirror.snapshot import json_bytes, parse_json
from mirror.validate import sha256
from support import example, modified


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vpngate-consumer-test-")
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / "cache"
        self.index, self.files = example()
        self.calls = []

    def read(self, url, limit):
        self.calls.append(url)
        if url.endswith("/latest.json"):
            return json_bytes(self.index)
        return next(body for path, body in self.files.items() if url.endswith("/" + path))

    def load(self, **kwargs):
        return load_snapshot("OWNER/REPO", cache=self.cache, read=self.read, **kwargs)

    def test_complete_cache_and_same_commit_urls(self):
        self.load()
        self.assertEqual(read_cache(self.cache)[0], self.index)
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(all("@" + self.index["data_commit"] in url for url in self.calls[1:]))

    def test_unregistered_identifier_survives_verified_cache_install(self):
        host = "_unregistered_vpn335506854"
        self.index, self.files = example(modified(HostName=host))
        self.load()
        index, files = read_cache(self.cache)
        self.assertEqual(index, self.index)
        self.assertEqual(files, self.files)
        self.assertIn(host, [row["hostname"] for row in parse_json(files["data/servers.json"])["servers"]])

    def test_cdn_hash_failure_uses_same_commit_raw(self):
        def read(url, limit):
            if "cdn.jsdelivr.net" in url:
                self.calls.append(url)
                return b"bad cached response"
            return self.read(url, limit)
        load_snapshot("OWNER/REPO", cache=self.cache, read=read)
        raw = [url for url in self.calls if "raw.githubusercontent.com" in url and "/data/" in url]
        self.assertEqual(len(raw), 3)
        self.assertTrue(all("/" + self.index["data_commit"] + "/" in url for url in raw))

    def test_hash_failure_retains_old_cache(self):
        self.load()
        old = (self.cache / "current.json").read_bytes()
        self.index["fetched_at"] = self.index["index_generated_at"] = "2026-09-01T01:00:00.000Z"
        self.index["data_commit"] = "b" * 40
        self.files["data/servers.json"] += b"bad"
        with self.assertRaises(MirrorError):
            self.load()
        self.assertEqual((self.cache / "current.json").read_bytes(), old)
        read_cache(self.cache)

    def test_old_and_unsupported_indexes_retain_cache(self):
        self.load()
        old = (self.cache / "current.json").read_bytes()
        for change in [{"schema_version": 2}, {"fetched_at": "2026-08-31T00:00:00.000Z"}]:
            saved = copy.deepcopy(self.index)
            self.index.update(change)
            with self.assertRaises(MirrorError):
                self.load()
            self.assertEqual((self.cache / "current.json").read_bytes(), old)
            self.index = saved

    def test_unchanged_data_refresh_avoids_downloads_and_data_writes(self):
        self.load()
        before = {str(p): p.stat().st_mtime_ns for p in self.cache.glob("snapshots/*/data/*")}
        self.calls.clear()
        self.index["fetched_at"] = self.index["index_generated_at"] = "2026-09-01T01:00:00.000Z"
        self.load()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in self.cache.glob("snapshots/*/data/*")})

    def test_cross_file_mismatch_cannot_install(self):
        document = parse_json(self.files["data/countries.json"])
        document["source_csv_sha256"] = "f" * 64
        self.files["data/countries.json"] = json_bytes(document)
        self.index["files"]["data/countries.json"].update(
            sha256=sha256(self.files["data/countries.json"]), bytes=len(self.files["data/countries.json"]))
        with self.assertRaises(MirrorError):
            self.load()
        self.assertFalse((self.cache / "current.json").exists())

    def test_pointer_failure_keeps_old_complete_snapshot(self):
        self.load()
        old = (self.cache / "current.json").read_bytes()
        index = copy.deepcopy(self.index)
        index["fetched_at"] = index["index_generated_at"] = "2026-09-01T01:00:00.000Z"
        original = os.replace
        def fail_pointer(source, target):
            if Path(target).name == "current.json":
                raise OSError("simulated disk failure")
            return original(source, target)
        with patch("mirror.consumer.os.replace", fail_pointer):
            with self.assertRaises(OSError):
                install_cache(self.cache, index, self.files)
        self.assertEqual((self.cache / "current.json").read_bytes(), old)
        read_cache(self.cache)

    def test_consumer_age_limit_is_optional_and_enforced_before_install(self):
        with self.assertRaisesRegex(MirrorError, "maximum age"):
            self.load(max_age_hours=0)
        self.assertFalse((self.cache / "current.json").exists())
