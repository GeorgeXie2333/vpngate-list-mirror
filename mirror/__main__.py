"""Offline build/verify, optional live validation, and scheduled publication."""

import argparse
import html
import http.client
import json
import os
import subprocess
import sys
import time
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


def timed(report, name, operation):
    started = time.monotonic()
    try:
        return operation()
    finally:
        report["durations_seconds"][name] = round(time.monotonic() - started, 3)


def run_code_check(command):
    # Regression tests do not need the publication credential.
    env = {key: value for key, value in os.environ.items()
           if key not in {"GH_TOKEN", "GITHUB_TOKEN"}}
    subprocess.run(command, cwd=Path(__file__).resolve().parents[1],
                   env=env, check=True, timeout=180)


def checked_source():
    """Overlap read-only preparation; require both test suites before returning."""
    commands = ([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                ["node", "--test", "tests/test_consumer.mjs"])
    with ThreadPoolExecutor(max_workers=3) as executor:
        source = executor.submit(fetch_source)
        checks = [executor.submit(run_code_check, command) for command in commands]
        for check in checks:
            check.result()
        return source.result()


def probe(repo, index):
    def one(request):
        path, name, url = request
        meta = index["files"][path]
        try:
            body = read_https(url, meta["bytes"])
            if len(body) != meta["bytes"] or sha256(body) != meta["sha256"]:
                raise MirrorError("Hash/length mismatch")
            check = {"verified_at": utc_now()}
        except (OSError, http.client.HTTPException, MirrorError) as exc:
            check = {"error": str(exc)[:200]}
        return path, name, check
    requests = [(path, name, url) for path in DATA_PATHS
                for name, url in zip(("cdn", "raw"), file_urls(repo, index["data_commit"], path))]
    checks = {path: {} for path in DATA_PATHS}
    with ThreadPoolExecutor(max_workers=6) as executor:
        for path, name, check in executor.map(one, requests):
            checks[path][name] = check
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Build data from an offline CSV; no index or publication")
    build.add_argument("--source-file", required=True)
    build.add_argument("--output", required=True)
    verify = sub.add_parser("verify", help="Verify an existing index and all its data files")
    verify.add_argument("--directory", default=".")
    sub.add_parser("check-live", help="Fetch and validate official data without publishing")
    sub.add_parser("sync", help="Check code, fetch, validate, publish and check public visibility")
    args = parser.parse_args(argv)
    started = time.monotonic()
    report = {"attempt_started_at": utc_now(), "durations_seconds": {}}
    try:
        if args.command == "sync":
            repo = os.environ.get("GITHUB_REPOSITORY", "")
            run_id = os.environ.get("GITHUB_RUN_ID", "")
            if os.environ.get("GITHUB_REF") != "refs/heads/main" or not repo or not run_id.isdigit():
                raise MirrorError("Publication requires a GitHub Actions run on main")
            if not os.environ.get("GH_TOKEN"):
                raise MirrorError("Publication requires the job's GITHUB_TOKEN")
        if args.command == "verify":
            root = Path(args.directory)
            index = parse_json((root / "latest.json").read_bytes())
            verify_files(index, {path: (root / path).read_bytes() for path in DATA_PATHS})
            report.update(status="verified", data_commit=index["data_commit"], server_count=index["server_count"])
        else:
            if args.command == "build":
                raw = Path(args.source_file).read_bytes()
            else:
                if args.command == "sync":
                    raw, fetched_at = timed(report, "checks_and_fetch", checked_source)
                else:
                    raw, fetched_at = timed(report, "fetch", fetch_source)
                report["fetched_at"] = fetched_at
            snapshot = timed(report, "build_validate", lambda: build_snapshot(raw))
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
                revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
                result = timed(report, "publication", lambda: publish(
                                 snapshot, remote=f"https://github.com/{repo}.git", source_revision=revision,
                                 fetched_at=fetched_at, generated_at=generated_at,
                                 run_url=f"https://github.com/{repo}/actions/runs/{run_id}",
                                 token=os.environ["GH_TOKEN"]))
                index = result.pop("index", None)
                report.update(result)
                if index:
                    report["visibility_probes"] = timed(report, "visibility_probes", lambda: probe(repo, index))
                    if any("error" in checks["cdn"] for checks in report["visibility_probes"].values()):
                        print("::warning::Snapshot was pushed, but CDN visibility is not fully confirmed. Check same-SHA Raw fallback.")
        return 0
    except (MirrorError, OSError, http.client.HTTPException, subprocess.SubprocessError) as exc:
        report.update(status="failed", error=str(exc)[:1000])
        return 1
    finally:
        report["durations_seconds"]["total"] = round(time.monotonic() - started, 3)
        summary(report)


if __name__ == "__main__":
    raise SystemExit(main())
