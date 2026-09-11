"""Publish a data commit followed by its index, with one fast-forward push."""

import base64
import os
import subprocess
import tempfile
from pathlib import Path

from . import DATA_PATHS, MirrorError
from .fetch import utc_now
from .snapshot import json_bytes, make_index, parse_json, verify_files, write_data
from .validate import parse_utc


def git_environment(token=None):
    env = os.environ.copy()
    env.update(GIT_TERMINAL_PROMPT="0", GIT_AUTHOR_NAME="github-actions[bot]",
               GIT_AUTHOR_EMAIL="41898282+github-actions[bot]@users.noreply.github.com",
               GIT_COMMITTER_NAME="github-actions[bot]",
               GIT_COMMITTER_EMAIL="41898282+github-actions[bot]@users.noreply.github.com")
    if token:
        # Authentication stays in the process environment, never in URLs,
        # command arguments, the checkout's config, or a persistent file.
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        env[f"GIT_CONFIG_KEY_{count}"] = "http.https://github.com/.extraheader"
        auth = base64.b64encode(("x-access-token:" + token).encode()).decode()
        env[f"GIT_CONFIG_VALUE_{count}"] = "AUTHORIZATION: basic " + auth
        env["GIT_CONFIG_COUNT"] = str(count + 1)
    return env


class Git:
    def __init__(self, cwd, env):
        self.cwd, self.env = cwd, env

    def run(self, *args, check=True):
        try:
            result = subprocess.run(["git", *args], cwd=self.cwd, env=self.env,
                                    capture_output=True, timeout=90)
        except subprocess.TimeoutExpired as exc:
            raise MirrorError("Git operation timed out") from exc
        if check and result.returncode:
            detail = result.stderr.decode("utf-8", "replace")[-600:].strip()
            raise MirrorError(f"Git {args[0]} failed: {detail}")
        return result

    def text(self, *args):
        return self.run(*args).stdout.decode("utf-8").strip()

    def blob(self, revision, path):
        result = self.run("show", f"{revision}:{path}", check=False)
        return result.stdout if result.returncode == 0 else None


def previous_snapshot(git, revision):
    raw_index = git.blob(revision, "latest.json")
    files = {path: git.blob(revision, path) for path in DATA_PATHS}
    if raw_index is None:
        if any(body is not None for body in files.values()):
            raise MirrorError("Data exists without a successful index; refusing to overwrite it")
        return None, None
    index = parse_json(raw_index)
    verify_files(index, files)
    return index, files


def safe_concurrent_changes(git, old, new):
    paths = git.text("diff", "--name-only", old, new).splitlines()
    # Documentation-only movement can be retained automatically. Changes to
    # code, workflow, schema or generated data require a fresh workflow run.
    return all(path.endswith(".md") or path in {"LICENSE", "NOTICE"} for path in paths)


def publish(snapshot, *, remote, source_revision, fetched_at, generated_at,
            run_url, branch="main", token=None, attempts=3, before_push=None):
    env = git_environment(token)
    with tempfile.TemporaryDirectory(prefix="vpngate-publish-") as directory:
        root = Path(directory) / "checkout"
        bootstrap = Git(directory, env)
        bootstrap.run("clone", "--no-checkout", "--depth", "1", "--single-branch",
                      "--branch", branch, "--", remote, str(root))
        git = Git(root, env)
        git.run("checkout", "-B", branch, f"origin/{branch}")
        base = git.text("rev-parse", "HEAD")
        if source_revision != base:
            git.run("fetch", "--depth", "1", "origin", source_revision)
            old_index, _ = previous_snapshot(git, base)
            if old_index and parse_utc(old_index["fetched_at"]) >= parse_utc(fetched_at):
                return {"status": "superseded", "data_commit": old_index["data_commit"], "fetched_at": fetched_at}
            if not safe_concurrent_changes(git, source_revision, base):
                raise MirrorError("Default branch changed in code or generated data; rerun from its latest commit")
        for attempt in range(1, attempts + 1):
            previous, old_files = previous_snapshot(git, base)
            if previous and parse_utc(previous["fetched_at"]) >= parse_utc(fetched_at):
                return {"status": "superseded", "data_commit": previous["data_commit"], "fetched_at": fetched_at}
            changed = old_files != snapshot.files
            if changed:
                write_data(snapshot, root)
                git.run("add", "--", *DATA_PATHS)
                git.run("commit", "-m", f"data: snapshot fetched {fetched_at}")
                data_commit = git.text("rev-parse", "HEAD")
                data_generated_at = generated_at
            else:
                data_commit = previous["data_commit"]
                data_generated_at = previous["generated_at"]
            index = make_index(snapshot, data_commit, fetched_at, data_generated_at, utc_now(), run_url)
            verify_files(index, snapshot.files)
            (root / "latest.json").write_bytes(json_bytes(index))
            git.run("add", "--", "latest.json")
            git.run("commit", "-m", f"index: successful fetch {fetched_at}")
            index_commit = git.text("rev-parse", "HEAD")
            if before_push:
                before_push(attempt, data_commit, index_commit)
            try:
                result = git.run("push", "origin", f"HEAD:refs/heads/{branch}", check=False)
                pushed = result.returncode == 0
            except MirrorError:
                # An ACK can be lost after the remote accepted the push.
                pushed = False
            if pushed:
                return {"status": "published", "data_changed": changed, "data_commit": data_commit,
                        "index_commit": index_commit, "fetched_at": fetched_at,
                        "push_confirmed_at": utc_now(), "push_attempts": attempt, "index": index}
            git.run("fetch", "--depth", "1", "origin", f"refs/heads/{branch}")
            remote_head = git.text("rev-parse", "FETCH_HEAD")
            observed, _ = previous_snapshot(git, remote_head)
            if remote_head == index_commit or observed == index:
                return {"status": "published", "data_changed": changed, "data_commit": data_commit,
                        "index_commit": index_commit, "fetched_at": fetched_at,
                        "push_confirmed_at": utc_now(), "push_attempts": attempt, "index": index}
            if observed and parse_utc(observed["fetched_at"]) >= parse_utc(fetched_at):
                return {"status": "superseded", "data_commit": observed["data_commit"], "fetched_at": fetched_at}
            if remote_head == base:
                raise MirrorError("Push was not accepted; check repository permissions and branch rules")
            if not safe_concurrent_changes(git, base, remote_head):
                raise MirrorError("Concurrent code/data change; publication stopped without overwriting it")
            if attempt == attempts:
                break
            # This checkout is owned by TemporaryDirectory. User checkouts are
            # never reset. Rebuild BOTH commits: rebasing an old index is unsafe.
            git.run("checkout", "-B", branch, remote_head)
            base = remote_head
        raise MirrorError(f"Publication race persisted for {attempts} attempts")
