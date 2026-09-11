"""Anonymous snapshot consumer with SHA-pinned fallback and atomic caching."""

import argparse
import http.client
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import DATA_PATHS, MirrorError
from .fetch import read_https
from .snapshot import json_bytes, parse_json, verify_files
from .validate import decode_config, parse_utc, sha256, validate_index

MAX_INDEX_BYTES = 65536


def repository_name(repo):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise MirrorError("Expected OWNER/REPO")
    return repo


def file_urls(repo, commit, path):
    return (f"https://cdn.jsdelivr.net/gh/{repo}@{commit}/{path}",
            f"https://raw.githubusercontent.com/{repo}/{commit}/{path}")


def download_file(repo, index, path, *, read=read_https):
    meta = index["files"][path]
    for url in file_urls(repo, index["data_commit"], path):
        try:
            body = read(url, meta["bytes"])
            if len(body) != meta["bytes"] or sha256(body) != meta["sha256"]:
                raise MirrorError("Response hash or length mismatch")
            return body
        except (OSError, http.client.HTTPException, MirrorError) as exc:
            last_error = exc
    raise MirrorError(f"CDN and same-commit Raw download failed: {path}") from last_error


def snapshot_directory(cache, index):
    # Derive the path exclusively from validated hashes, never from URL/path
    # strings supplied by a remote manifest.
    digest = sha256(json_bytes(index["files"]))[:16]
    return Path(cache) / "snapshots" / f"{index['data_commit']}-{digest}"


def read_cache(cache):
    pointer = Path(cache) / "current.json"
    if not pointer.exists():
        return None, None
    if pointer.stat().st_size > MAX_INDEX_BYTES:
        raise MirrorError("Cached index is oversized")
    index = validate_index(parse_json(pointer.read_bytes()))
    root = snapshot_directory(cache, index)
    files = read_cached_files(root, index)
    verify_files(index, files)
    return index, files


def read_cached_files(root, index):
    files = {}
    for path in DATA_PATHS:
        target = root / path
        if target.stat().st_size != index["files"][path]["bytes"]:
            raise MirrorError(f"Cached file size mismatch: {path}")
        files[path] = target.read_bytes()
    return files


def check_freshness(index, previous=None, *, now=None, max_age_hours=None):
    fetched = parse_utc(index["fetched_at"])
    now = now or datetime.now(timezone.utc)
    if fetched > now + timedelta(minutes=5):
        raise MirrorError("Index timestamp is unexpectedly in the future")
    if previous:
        old = parse_utc(previous["fetched_at"])
        if fetched < old:
            raise MirrorError("Refusing an older index; retained the newer cache")
        if fetched == old and (index["data_commit"] != previous["data_commit"] or index["files"] != previous["files"]):
            raise MirrorError("Conflicting snapshots for the same fetch timestamp")
    age = (now - fetched).total_seconds() / 3600
    if max_age_hours is not None and age > max_age_hours:
        raise MirrorError("Snapshot exceeds the consumer's maximum age")
    return age


def atomic_bytes(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".pending-", delete=False) as output:
            temporary = Path(output.name)
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def install_cache(cache, index, files):
    verify_files(index, files)
    previous, _ = read_cache(cache)
    check_freshness(index, previous)
    root = snapshot_directory(cache, index)
    if not root.exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent, prefix=".pending-") as directory:
            staged = Path(directory) / "snapshot"
            for path in DATA_PATHS:
                body = files[path]
                target = staged / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            os.replace(staged, root)
    else:
        verify_files(index, read_cached_files(root, index))
    # The complete snapshot is present before the one atomic pointer change.
    atomic_bytes(Path(cache) / "current.json", json_bytes(index))


def load_snapshot(repo, *, cache=".cache/vpngate", index_file=None, read=read_https, max_age_hours=None):
    repository_name(repo)
    previous, old_files = read_cache(cache)
    if index_file:
        path = Path(index_file)
        if path.stat().st_size > MAX_INDEX_BYTES:
            raise MirrorError("Index is oversized")
        raw = path.read_bytes()
    else:
        raw = read(f"https://raw.githubusercontent.com/{repo}/main/latest.json", MAX_INDEX_BYTES)
    index = validate_index(parse_json(raw))
    check_freshness(index, previous, max_age_hours=max_age_hours)
    if previous and index["data_commit"] == previous["data_commit"] and index["files"] == previous["files"]:
        files = old_files
    else:
        files = {path: download_file(repo, index, path, read=read) for path in DATA_PATHS}
    verify_files(index, files)
    install_cache(cache, index, files)
    return index, files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="GeorgeXie2333/vpngate-list-mirror")
    parser.add_argument("--cache", default=".cache/vpngate")
    parser.add_argument("--index-file")
    parser.add_argument("--country", default="JP")
    parser.add_argument("--output", default="selected.ovpn")
    parser.add_argument("--max-age-hours", type=float)
    args = parser.parse_args(argv)
    try:
        index, files = load_snapshot(args.repo, cache=args.cache, index_file=args.index_file,
                                     max_age_hours=args.max_age_hours)
        age = check_freshness(index)
        if age > 3:
            print(f"Warning: last successful fetch was {age:.1f} hours ago", file=sys.stderr)
        servers = parse_json(files["data/servers.json"])["servers"]
        selected = next((server for server in servers if server["country_code"] == args.country.upper()), None)
        if selected is None:
            raise MirrorError("No matching node in this snapshot; no configuration was written")
        config = decode_config(selected["openvpn_config_base64"])
        atomic_bytes(args.output, config)
        print(f"Verified {index['server_count']} nodes; fetched {index['fetched_at']}; data {index['data_commit']}")
        print(f"Saved {args.output} for {selected['id']}; configuration was not executed")
        return 0
    except (MirrorError, OSError) as exc:
        print(f"Refresh/export failed: {exc}. Existing successful cache was retained.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
