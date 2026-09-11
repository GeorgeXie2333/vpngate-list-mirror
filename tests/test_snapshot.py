import copy
import json
import unittest
from pathlib import Path

from mirror import MirrorError
from mirror.snapshot import build_snapshot, json_bytes, parse_json, verify_files
from mirror.validate import sha256, validate_index
from support import example, fixture


class SnapshotTests(unittest.TestCase):
    def test_deterministic_and_counts(self):
        self.assertEqual(build_snapshot(fixture()).files, build_snapshot(fixture()).files)
        index, files = example()
        verify_files(index, files)
        countries = parse_json(files["data/countries.json"])
        self.assertEqual(sum(c["server_count"] for c in countries["countries"]), index["server_count"])

    def test_hash_and_mixed_version_failures(self):
        index, files = example()
        corrupted = dict(files)
        corrupted["data/servers.json"] += b" "
        with self.assertRaises(MirrorError):
            verify_files(index, corrupted)
        mixed = dict(files)
        servers = parse_json(mixed["data/servers.json"])
        servers["source_csv_sha256"] = "f" * 64
        mixed["data/servers.json"] = json_bytes(servers)
        index["files"]["data/servers.json"].update(sha256=sha256(mixed["data/servers.json"]), bytes=len(mixed["data/servers.json"]))
        with self.assertRaisesRegex(MirrorError, "Cross-file"):
            verify_files(index, mixed)

    def test_compatible_optional_fields(self):
        index, files = example()
        index["optional_future_field"] = "allowed"
        document = parse_json(files["data/servers.json"])
        document["servers"][0]["optional_future_field"] = "allowed"
        files["data/servers.json"] = json_bytes(document)
        index["files"]["data/servers.json"].update(sha256=sha256(files["data/servers.json"]), bytes=len(files["data/servers.json"]))
        verify_files(index, files)

    def test_manifest_shape(self):
        index, _ = example()
        mutations = [{"schema_version": 2}, {"schema_version": True}, {"data_commit": "main"},
                     {"server_count": True}, {"fetched_at": "2026-09-01T00:00:00+08:00"},
                     {"source_url": "http://www.vpngate.net/api/iphone/"}, {"country_count": 3}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(MirrorError):
                validate_index({**index, **mutation})
        bad = copy.deepcopy(index)
        bad["files"]["data/servers.json"]["bytes"] = 1 << 30
        with self.assertRaises(MirrorError):
            validate_index(bad)

    def test_schema_files_describe_produced_fields(self):
        root = Path(__file__).resolve().parents[1] / "schemas/v1"
        index, files = example()
        for name, instance in [("latest", index), ("servers", parse_json(files["data/servers.json"])),
                               ("countries", parse_json(files["data/countries.json"]))]:
            schema = json.loads((root / f"{name}.schema.json").read_text())
            self.assertTrue(set(schema["required"]).issubset(instance))
            self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
