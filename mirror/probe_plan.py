"""Deterministic task reservations in Git; no network or KV scheduling writes."""

from datetime import datetime, timezone

from .pool import bucket_id, descriptor, require, target_record
from .snapshot import json_bytes, parse_json
from .validate import parse_utc, sha256

SLOT_SECONDS = 300
NORMAL_INTERVAL = 14400
RETRY_INTERVAL = 3600
HORIZON = 10800
COMMIT_WINDOW = 1800
MANIFEST = "pool/task-plan.json"


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def task_path(worker, slot):
    return f"pool/tasks/{worker}-{slot}.json"


def attempt_time(row):
    probe = row["tcp_probe"]
    value = probe.get("last_attempted_at") or probe["checked_at"]
    return parse_utc(value).timestamp() if value else None


def retryable(row):
    probe = row["tcp_probe"]
    return (probe.get("last_attempt_status") or probe["status"]) == "unknown"


def task_target(row):
    checked = attempt_time(row)
    due = (checked + (RETRY_INTERVAL if retryable(row) else NORMAL_INTERVAL)
           if checked is not None else parse_utc(row["first_seen_at"]).timestamp())
    return {**target_record(row), "next_probe_at": iso(due)}


def build_tasks(rows, fetched_at, previous=None):
    """Reserve the next 30 minutes; replan the later outage fallback each publish.

    Keep past assignments for delayed Cron events. Recent/future reservations
    prevent both Workers or a plan change from immediately assigning a node again.
    A missed result is only reserved for 30 minutes, not counted as an attempt.
    """
    now = parse_utc(fetched_at).timestamp()
    nodes = {r["id"]: r for r in rows if r["probe_targets"]}
    held = {}
    for path, body in (previous or {}).items():
        if not path.startswith("pool/tasks/"):
            continue
        task = parse_json(body)
        when = parse_utc(task["scheduled_at"]).timestamp()
        if now - HORIZON <= when <= now + COMMIT_WINDOW:
            # Drop deleted/config-changed targets; never replace a reserved ID.
            task["targets"] = [task_target(nodes[t["id"]]) for t in task["targets"]
                               if t["id"] in nodes and
                               nodes[t["id"]]["openvpn_config_sha256"] == t["config_sha256"]]
            held[path] = task
    virtual = {identifier: attempt_time(row) for identifier, row in nodes.items()}
    for task in held.values():
        when = parse_utc(task["scheduled_at"]).timestamp()
        if when >= now - COMMIT_WINDOW:
            for target in task["targets"]:
                old = virtual[target["id"]]
                virtual[target["id"]] = max(old if old is not None else when, when)
    partitions = [[r for r in nodes.values() if bucket_id(r["id"]) // 72 == worker] for worker in (0, 1)]
    tasks = dict(held)
    first = int(now) // SLOT_SECONDS
    for slot in range(first, first + HORIZON // SLOT_SECONDS + 1):
        for worker in (0, 1):
            when = slot * SLOT_SECONDS + (120 if worker == 0 else 240)
            path = task_path(worker, slot)
            if path in tasks or not now <= when <= now + HORIZON:
                continue
            def priority(row):
                seen = virtual[row["id"]]
                # All overdue regular checks outrank opportunistic unknown retries.
                regular = seen is None or seen + NORMAL_INTERVAL <= when
                age = seen if seen is not None else parse_utc(row["first_seen_at"]).timestamp() - NORMAL_INTERVAL
                return (not regular, age, row["id"])
            candidates = [r for r in partitions[worker] if virtual[r["id"]] is None or
                          virtual[r["id"]] + NORMAL_INTERVAL <= when or
                          (retryable(r) and virtual[r["id"]] + RETRY_INTERVAL <= when)]
            chosen, endpoints = [], set()
            for row in sorted(candidates, key=priority):
                extra = {(e["ip"], e["port"]) for e in row["probe_targets"]} - endpoints
                # Reserve five of the forty endpoint slots for current-source controls.
                if len(chosen) == 35 or len(endpoints) + len(extra) > 35:
                    continue
                chosen.append(task_target(row))
                endpoints.update(extra)
                virtual[row["id"]] = when
            tasks[path] = {"schema_version": 2, "worker_id": worker, "slot": slot,
                           "scheduled_at": iso(when), "targets": chosen}
    return {path: json_bytes(task) for path, task in sorted(tasks.items())}


def attach_tasks(files, plan, tasks):
    manifest = {"schema_version": 2, "normal_interval_seconds": NORMAL_INTERVAL,
                "retry_interval_seconds": RETRY_INTERVAL, "tasks": {}}
    for path, body in sorted(tasks.items()):
        task = parse_json(body)
        require(len(body) <= 65536, "Task exceeds Worker input limit")
        files[path] = body
        manifest["tasks"][f"{task['worker_id']}:{task['slot']}"] = {"path": path, **descriptor(body)}
    files[MANIFEST] = json_bytes(manifest)
    require(len(files[MANIFEST]) <= 65536, "Task manifest exceeds Worker input limit")
    plan["task_protocol_version"] = 2
    plan["task_manifest"] = {"path": MANIFEST, **descriptor(files[MANIFEST])}


def verified_tasks(files, plan, rows, fetched_at):
    """Validate the entire published task graph before accepting/publishing a pool."""
    if "task_protocol_version" not in plan:
        return None  # Historical v1 snapshots remain valid.
    require(plan["task_protocol_version"] == 2, "Unsupported task protocol")
    meta = plan["task_manifest"]
    require(meta["path"] == MANIFEST, "Invalid task manifest path")
    body = files[MANIFEST]
    require(len(body) == meta["bytes"] <= 65536 and sha256(body) == meta["sha256"], "Task manifest hash mismatch")
    manifest = parse_json(body)
    require(manifest["schema_version"] == 2 and manifest["normal_interval_seconds"] == NORMAL_INTERVAL
            and manifest["retry_interval_seconds"] == RETRY_INTERVAL, "Invalid task intervals")
    require(isinstance(manifest["tasks"], dict) and len(manifest["tasks"]) <= 148, "Oversized task manifest")
    now, found = parse_utc(fetched_at).timestamp(), {}
    nodes = {row["id"]: row for row in rows}
    for key, meta in manifest["tasks"].items():
        body = files[meta["path"]]
        require(len(body) == meta["bytes"] <= 65536 and sha256(body) == meta["sha256"], "Task hash mismatch")
        task = parse_json(body)
        worker, slot = task["worker_id"], task["slot"]
        require(type(worker) is int and worker in (0, 1) and type(slot) is int and slot >= 0, "Invalid task identity")
        require(key == f"{worker}:{slot}" and meta["path"] == task_path(worker, slot)
                and task["schema_version"] == 2, "Mixed task identity")
        when = slot * SLOT_SECONDS + (120 if worker == 0 else 240)
        require(task["scheduled_at"] == iso(when) and now - HORIZON <= when <= now + HORIZON, "Invalid task time")
        targets = task["targets"]
        require(isinstance(targets, list) and len(targets) <= 35
                and len({t["id"] for t in targets}) == len(targets), "Invalid task targets")
        endpoints = set()
        for target in targets:
            row = nodes.get(target["id"])
            require(row and row["probe_targets"] and bucket_id(row["id"]) // 72 == worker
                    and target == task_target(row), "Task target does not match pool")
            endpoints.update((e["ip"], e["port"]) for e in target["endpoints"])
        require(len(endpoints) <= 35, "Task endpoint limit exceeded")
        found[meta["path"]] = body
    return found
