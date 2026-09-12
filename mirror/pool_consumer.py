"""Anonymous, SHA-pinned pool catalogs with lazy, verified configuration downloads."""

import argparse
import http.client
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import MirrorError
from .consumer import atomic_bytes, download_file, repository_name
from .fetch import read_https
from .pool import CATALOG_PATHS, MAX_INDEX, require, validate_pool_index, verify_catalog, verify_config
from .snapshot import json_bytes, parse_json
from .validate import parse_utc, sha256


def freshness(index, previous=None, max_age_hours=None):
    instant = parse_utc(index["source_fetched_at"])
    now = datetime.now(timezone.utc)
    require(instant <= now + timedelta(minutes=5), "Future pool index")
    if max_age_hours is not None:
        require((now - instant).total_seconds() <= max_age_hours * 3600, "Pool index is too old")
    if previous:
        old = parse_utc(previous["source_fetched_at"])
        require(instant >= old, "Refusing an older pool index")
        require(instant != old or (index["data_commit"], index["files"]) == (previous["data_commit"], previous["files"]), "Conflicting pool index")


def catalog_directory(cache, index):
    return Path(cache) / "catalogs" / (index["data_commit"] + "-" + sha256(json_bytes(index["files"]))[:16])


def read_catalog_cache(cache):
    pointer = Path(cache) / "current.json"
    if not pointer.exists():
        return None, None
    require(pointer.stat().st_size <= MAX_INDEX, "Oversized cached pool index")
    index = validate_pool_index(parse_json(pointer.read_bytes()))
    root = catalog_directory(cache, index)
    files = {}
    for path in CATALOG_PATHS:
        target = root / path
        require(target.stat().st_size == index["files"][path]["bytes"], "Cached catalog size mismatch")
        files[path] = target.read_bytes()
    verify_catalog(index, files)
    return index, files


def load_pool(repo, *, cache=".cache/vpngate-pool", read=read_https, max_age_hours=None):
    repository_name(repo)
    previous, old_files = read_catalog_cache(cache)
    index = validate_pool_index(parse_json(read(f"https://raw.githubusercontent.com/{repo}/main/pool/latest.json", MAX_INDEX)))
    freshness(index, previous, max_age_hours)
    files = (old_files if previous and previous["files"] == index["files"] else
             {path: download_file(repo, index, path, read=read) for path in CATALOG_PATHS})
    rows = verify_catalog(index, files)
    root = catalog_directory(cache, index)
    if not root.exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent, prefix=".pending-") as directory:
            staged = Path(directory) / "catalog"
            for path, body in files.items():
                target = staged / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            os.replace(staged, root)
    else:
        # A leftover candidate directory is not trusted merely because its name
        # matches. Never point a valid old cache at partial/corrupt staged bytes.
        existing = {}
        for path in CATALOG_PATHS:
            target = root / path
            require(target.stat().st_size == index["files"][path]["bytes"], "Existing catalog size mismatch")
            existing[path] = target.read_bytes()
        verify_catalog(index, existing)
    current, _ = read_catalog_cache(cache)
    freshness(index, current, max_age_hours)
    atomic_bytes(Path(cache) / "current.json", json_bytes(index))
    return index, rows


def load_pool_config(repo, index, row, *, cache=".cache/vpngate-pool", read=read_https):
    repository_name(repo)
    validate_pool_index(index)
    # Caller supplies a row from the catalog returned by load_pool.
    meta = row["config"]
    require(meta["path"] == f"pool/configs/{row['openvpn_config_sha256']}.json", "Unsafe config path")
    target = Path(cache) / "configs" / (meta["sha256"] + ".json")
    require(len(meta["sha256"]) == 64 and all(c in "0123456789abcdef" for c in meta["sha256"]), "Unsafe config hash")
    if target.exists():
        require(target.stat().st_size == meta["bytes"], "Cached config size mismatch")
        body = target.read_bytes()
    else:
        body = download_file(repo, {"data_commit": index["data_commit"], "files": {meta["path"]: meta}}, meta["path"], read=read)
    decoded = verify_config(row, body)
    if not target.exists():
        atomic_bytes(target, body)
    return decoded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="GeorgeXie2333/vpngate-list-mirror")
    parser.add_argument("--cache", default=".cache/vpngate-pool")
    parser.add_argument("--country", default="JP")
    parser.add_argument("--output", default="selected.ovpn")
    parser.add_argument("--max-age-hours", type=float)
    parser.add_argument("--tcp-reachable", action="store_true", help="Require a TCP success checked within 12 hours")
    args = parser.parse_args(argv)
    try:
        index, rows = load_pool(args.repo, cache=args.cache, max_age_hours=args.max_age_hours)
        matches = [r for r in rows if r["country_code"] == args.country.upper()]
        if args.tcp_reachable:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
            matches = [r for r in matches if r["tcp_probe"]["status"] == "reachable" and r["tcp_probe"]["checked_at"]
                       and parse_utc(r["tcp_probe"]["checked_at"]) >= cutoff]
        require(matches, "No matching pool node; output was retained")
        matches.sort(key=lambda r: (-parse_utc(r["last_seen_at"]).timestamp(), r["id"]))
        row = matches[0]
        body = load_pool_config(args.repo, index, row, cache=args.cache)
        atomic_bytes(args.output, body)
        print(f"Verified {len(rows)} pool nodes; source {index['source_fetched_at']}; node last seen {row['last_seen_at']}")
        print(f"Saved {args.output}; TCP status {row['tcp_probe']['status']}; no VPN was started")
        return 0
    except (MirrorError, OSError, http.client.HTTPException, KeyError, TypeError, ValueError) as exc:
        print(f"Pool refresh/export failed: {exc}. Previous verified cache/output retained.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
