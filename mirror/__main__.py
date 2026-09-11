"""Offline build/verify, optional live validation, and scheduled publication."""

import argparse
import html
import http.client
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import DATA_PATHS, MirrorError
from .consumer import file_urls
from .fetch import fetch_source, read_https, utc_now
from .publish import publish
from .snapshot import build_snapshot, parse_json, verify_files, write_data
from .validate import sha256


def summary(report):
    print(json.dumps(report, ensure_ascii=False, indent=2))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        lines = ["## VPN Gate directory mirror", "", "| Field | Value |", "| --- | --- |"]
        for key, value in report.items():
            display = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            lines.append(f"| {html.escape(key)} | {html.escape(display).replace('|', '&#124;').replace(chr(10), ' ')} |")
        with open(path, "a", encoding="utf-8", newline="\n") as output:
            output.write("\n".join(lines) + "\n")


def probe(repo, index):
    def one(path):
        checks = {}
        meta = index["files"][path]
        for name, url in zip(("cdn", "raw"), file_urls(repo, index["data_commit"], path)):
            try:
                body = read_https(url, meta["bytes"])
                if len(body) != meta["bytes"] or sha256(body) != meta["sha256"]:
                    raise MirrorError("Hash/length mismatch")
                checks[name] = {"verified_at": utc_now()}
            except (OSError, http.client.HTTPException, MirrorError) as exc:
                checks[name] = {"error": str(exc)[:200]}
        return path, checks
    with ThreadPoolExecutor(max_workers=3) as executor:
        return dict(executor.map(one, DATA_PATHS))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Build data from an offline CSV; no index or publication")
    build.add_argument("--source-file", required=True)
    build.add_argument("--output", required=True)
    verify = sub.add_parser("verify", help="Verify an existing index and all its data files")
    verify.add_argument("--directory", default=".")
    sub.add_parser("check-live", help="Fetch and validate official data without publishing")
    sub.add_parser("sync", help="Fetch, validate, publish and check public visibility")
    args = parser.parse_args(argv)
    report = {"attempt_started_at": utc_now()}
    try:
        if args.command == "verify":
            root = Path(args.directory)
            index = parse_json((root / "latest.json").read_bytes())
            verify_files(index, {path: (root / path).read_bytes() for path in DATA_PATHS})
            report.update(status="verified", data_commit=index["data_commit"], server_count=index["server_count"])
        else:
            if args.command == "build":
                raw = Path(args.source_file).read_bytes()
            else:
                raw, fetched_at = fetch_source()
                report["fetched_at"] = fetched_at
            snapshot = build_snapshot(raw)
            generated_at = utc_now()
            report.update(source_record_count=snapshot.source_record_count, server_count=snapshot.server_count,
                          country_count=snapshot.country_count,
                          duplicate_count=snapshot.source_record_count - snapshot.server_count,
                          files=snapshot.manifest())
            if args.command == "build":
                write_data(snapshot, args.output)
                report["status"] = "built_offline"
            elif args.command == "check-live":
                report["status"] = "validated_without_publication"
            else:
                repo = os.environ.get("GITHUB_REPOSITORY", "")
                run_id = os.environ.get("GITHUB_RUN_ID", "")
                if os.environ.get("GITHUB_REF") != "refs/heads/main" or not repo or not run_id.isdigit():
                    raise MirrorError("Publication requires a GitHub Actions run on main")
                if not os.environ.get("GH_TOKEN"):
                    raise MirrorError("Publication requires the job's GITHUB_TOKEN")
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
                result = publish(snapshot, remote=f"https://github.com/{repo}.git", source_revision=revision,
                                 fetched_at=fetched_at, generated_at=generated_at,
                                 run_url=f"https://github.com/{repo}/actions/runs/{run_id}",
                                 token=os.environ["GH_TOKEN"])
                index = result.pop("index", None)
                report.update(result)
                if index:
                    report["visibility_probes"] = probe(repo, index)
                    if any("error" in checks["cdn"] for checks in report["visibility_probes"].values()):
                        print("::warning::Snapshot was pushed, but CDN visibility is not fully confirmed. Check same-SHA Raw fallback.")
        summary(report)
        return 0
    except (MirrorError, OSError, http.client.HTTPException, subprocess.SubprocessError) as exc:
        report.update(status="failed", error=str(exc)[:1000])
        summary(report)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
