"""Deterministic data files and cross-file verification."""

import json
from dataclasses import dataclass
from pathlib import Path

from . import DATA_PATHS, MAX_FILE_BYTES, SCHEMA_VERSION, SOURCE_URL, MirrorError
from .parse import parse_csv
from .validate import sha256, validate_index


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MirrorError("Duplicate JSON object key")
        result[key] = value
    return result


def parse_json(body):
    try:
        return json.loads(body, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(MirrorError("Non-finite JSON number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MirrorError("Invalid JSON document") from exc


@dataclass
class Snapshot:
    files: dict
    source_record_count: int
    server_count: int
    country_count: int

    def manifest(self):
        return {path: {"sha256": sha256(body), "bytes": len(body), "server_count": self.server_count}
                for path, body in self.files.items()}


def build_snapshot(raw):
    servers, source_count = parse_csv(raw)
    grouped = {}
    for server in servers:
        country = grouped.setdefault(server["country_code"], {"names": set(), "server_count": 0})
        country["server_count"] += 1
        if server["country_name"]:
            country["names"].add(server["country_name"])
    countries = [{"code": code, "names": sorted(info["names"]), "server_count": info["server_count"]}
                 for code, info in sorted(grouped.items(), key=lambda pair: (pair[0] is None, pair[0] or ""))]
    common = {"schema_version": SCHEMA_VERSION, "source_csv_sha256": sha256(raw), "server_count": len(servers)}
    files = {
        DATA_PATHS[0]: raw,
        DATA_PATHS[1]: json_bytes({**common, "servers": servers}),
        DATA_PATHS[2]: json_bytes({**common, "countries": countries}),
    }
    if any(len(body) > MAX_FILE_BYTES for body in files.values()):
        raise MirrorError("Generated file exceeds size limit")
    return Snapshot(files, source_count, len(servers), len(countries))


def make_index(snapshot, commit, fetched_at, generated_at, index_generated_at, run_url):
    index = {
        "schema_version": SCHEMA_VERSION, "source_url": SOURCE_URL, "source_updated_at": None,
        "fetched_at": fetched_at, "generated_at": generated_at, "index_generated_at": index_generated_at,
        "data_commit": commit, "source_record_count": snapshot.source_record_count,
        "server_count": snapshot.server_count, "country_count": snapshot.country_count,
        "files": snapshot.manifest(), "workflow_run_url": run_url,
    }
    return validate_index(index)


def compatible(actual, expected):
    """Allow future optional fields, while enforcing every current v1 field."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return all(key in actual and compatible(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(compatible(a, b) for a, b in zip(actual, expected))
    return actual == expected


def verify_files(index, files):
    validate_index(index)
    for path in DATA_PATHS:
        body = files.get(path)
        meta = index["files"][path]
        if not isinstance(body, bytes) or len(body) != meta["bytes"] or sha256(body) != meta["sha256"]:
            raise MirrorError(f"Integrity check failed: {path}")
    expected = build_snapshot(files[DATA_PATHS[0]])
    if (index["server_count"], index["source_record_count"], index["country_count"]) != (
            expected.server_count, expected.source_record_count, expected.country_count):
        raise MirrorError("Manifest count mismatch")
    for path in DATA_PATHS[1:]:
        if not compatible(parse_json(files[path]), parse_json(expected.files[path])):
            raise MirrorError(f"Cross-file snapshot mismatch: {path}")
    return expected


def write_data(snapshot, root):
    root = Path(root)
    for path in DATA_PATHS:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(snapshot.files[path])
