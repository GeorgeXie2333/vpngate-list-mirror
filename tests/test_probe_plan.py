import base64
import copy
import json
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mirror import MirrorError
from mirror.pool import apply_batches, bucket_id, build_pool, verify_pool
from mirror.probe_plan import build_tasks, attach_tasks, verified_tasks, task_path
from mirror.snapshot import parse_json
from mirror.validate import parse_utc
from test_pool import at, source, nodes, report, index


def fleet(count):
    row = nodes(build_pool(source(), at()))[0]
    output = []
    for n in range(count):
        item = copy.deepcopy(row)
        item["id"] = "v1:" + f"{n:08x}" + "0" * 56
        item["probe_targets"] = [{"ip": item["ip"], "port": 1000 + n}]
        output.append(item)
    return output


class ProbePlanTests(unittest.TestCase):
    def test_packs_fill_spare_capacity_without_duplicate_targets_in_half_hour(self):
        rows = fleet(2683)
        files = build_tasks(rows, at())
        start = parse_utc(at()).timestamp()
        first = [parse_json(b) for b in files.values() if parse_utc(parse_json(b)["scheduled_at"]).timestamp() < start + 1800]
        chosen = [r for t in first for r in t["targets"]]
        self.assertEqual(len(first), 12)
        self.assertEqual(len(chosen), 420)
        self.assertEqual(len({r["id"] for r in chosen}), 420)
        plan, all_files = {}, {}
        attach_tasks(all_files, plan, files)
        self.assertLess(len(all_files["pool/task-plan.json"]), 65536)
        self.assertTrue(all(len(b) <= 65536 for b in files.values()))
        self.assertEqual(verified_tasks(all_files, plan, rows, at()), files)
        self.assertEqual(files, build_tasks(list(reversed(rows)), at()))

    def test_four_hour_checks_outrank_one_hour_unknown_retries(self):
        rows = fleet(40)  # All belong to Worker 0.
        for row in rows:
            row["tcp_probe"].update(status="unknown", checked_at=at(1))
        rows[-1]["tcp_probe"].update(status="reachable", checked_at=at())
        rows[-2]["tcp_probe"].update(status="reachable", checked_at=at(3))
        files = build_tasks(rows, at(4))
        slot = int(parse_utc(at(4)).timestamp()) // 300
        selected = parse_json(files[task_path(0, slot)])["targets"]
        self.assertEqual(selected[0]["id"], rows[-1]["id"])
        self.assertNotIn(rows[-2]["id"], {t["id"] for t in selected})
        self.assertEqual(len(selected), 35)
        times = {}
        for task in sorted(map(parse_json, files.values()), key=lambda t: t["scheduled_at"]):
            for target in task["targets"]:
                instant = parse_utc(task["scheduled_at"]).timestamp()
                if target["id"] in times:
                    self.assertGreaterEqual(instant - times[target["id"]], 3600)
                times[target["id"]] = instant

    def test_reservations_survive_new_plan_and_changed_config_is_dropped(self):
        rows = fleet(200)
        before = build_tasks(rows, at())
        after = build_tasks(rows, at(.5), before)
        now = parse_utc(at(.5)).timestamp()
        for path, body in before.items():
            task = parse_json(body)
            if now <= parse_utc(task["scheduled_at"]).timestamp() <= now + 1800:
                self.assertEqual(parse_json(after[path])["targets"], task["targets"])
        changed = copy.deepcopy(rows)
        changed[0]["openvpn_config_sha256"] = "f" * 64
        updated = build_tasks(changed, at(.01), before)
        first_slot = int(parse_utc(at()).timestamp()) // 300
        self.assertNotIn(rows[0]["id"], {r["id"] for r in parse_json(updated[task_path(0, first_slot)])["targets"]})
        recovered = build_tasks(rows, at(4), before)
        self.assertTrue(all(parse_utc(parse_json(b)["scheduled_at"]) >= parse_utc(at(1)) for b in recovered.values()))
        self.assertTrue(any(parse_json(b)["targets"] for b in recovered.values()))

    def test_multiple_endpoints_reserve_space_for_controls(self):
        rows = fleet(40)
        for i, row in enumerate(rows):
            row["probe_targets"] = [{"ip": row["ip"], "port": 1000 + i * 8 + p} for p in range(8)]
        files = build_tasks(rows, at())
        self.assertEqual(max(len(parse_json(b)["targets"]) for b in files.values()), 4)

    def test_hash_or_identity_tampering_cannot_validate(self):
        rows = fleet(80)
        files, plan = {}, {}
        attach_tasks(files, plan, build_tasks(rows, at()))
        path = next(p for p in files if p.startswith("pool/tasks/"))
        files[path] += b" "
        with self.assertRaises(MirrorError):
            verified_tasks(files, plan, rows, at())

    def test_all_deferred_targets_remain_schedulable_and_manifest_stays_bounded(self):
        rows = fleet(5000)
        previous = {}
        for hour in (0, .5, 1, 1.5, 2, 2.5, 3, 3.5):
            previous = build_tasks(rows, at(hour), previous)
        # No observations arrived. Recent reservations expire, so work keeps
        # rotating instead of manufacturing a four-hour checked timestamp.
        manifest, files = {}, {}
        attach_tasks(files, manifest, previous)
        self.assertLessEqual(len(previous), 148)
        self.assertLess(len(files["pool/task-plan.json"]), 65536)
        self.assertEqual(verified_tasks(files, manifest, rows, at(3.5)), previous)
        self.assertTrue(all(r["tcp_probe"]["checked_at"] is None for r in rows))

    def test_task_schemas_match_producer_and_resolve_local_references(self):
        pool = build_pool(source(), at())
        root = Path(__file__).resolve().parents[1] / "schemas/probe/v2"
        values = {"task-plan": parse_json(pool.files["pool/task-plan.json"]),
                  "task": next(parse_json(b) for p, b in pool.files.items() if p.startswith("pool/tasks/"))}
        for name, value in values.items():
            schema = json.loads((root / f"{name}.schema.json").read_text())
            self.assertEqual(schema["properties"]["schema_version"]["const"], value["schema_version"])
            self.assertTrue(set(schema["required"]).issubset(value))
        for name in ("task", "batch"):
            schema = (root / f"{name}.schema.json").read_text()
            self.assertIn("../../pool/v1/common.schema.json", schema)
            self.assertTrue((root / "../../pool/v1/common.schema.json").is_file())

    def test_python_plan_is_consumed_by_worker_and_merged_without_real_network(self):
        pool = build_pool(source(), at())
        task = next(parse_json(b) for p, b in pool.files.items() if p.startswith("pool/tasks/") and parse_json(b)["targets"])
        env = {"files": {p: base64.b64encode(b).decode() for p, b in pool.files.items()},
               "index": index(pool), "task": task}
        script = """
import {scheduled} from './workers/src/core.mjs';
let input=''; for await (const chunk of process.stdin) input+=chunk;
const data=JSON.parse(input), now=Date.parse(data.task.scheduled_at), writes=[];
await scheduled({scheduledTime:now},{WORKER_ID:data.task.worker_id, REPOSITORY:'test/repo',
 RESULTS:{put:async(key,body,options)=>writes.push({key,body:JSON.parse(body),options})}},
 ()=>{throw new Error('real sockets forbidden');}, {now:()=>now,
 read:async url=>url.endsWith('/latest.json')?new TextEncoder().encode(JSON.stringify(data.index)):
   new Uint8Array(Buffer.from(data.files[url.split('/'+'a'.repeat(40)+'/')[1]],'base64')),
 attempt:async endpoint=>({...endpoint,status:'reachable',error:null,connect_ms:12})});
process.stdout.write(JSON.stringify(writes));
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(env),
                                text=True, capture_output=True, check=True, timeout=15,
                                cwd=Path(__file__).resolve().parents[1])
        writes = json.loads(result.stdout)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["options"]["expirationTtl"], 259200)
        batch = writes[0]["body"]
        self.assertEqual(batch["schema_version"], 2)
        new = build_pool(source(), at(.1), previous=pool.files, batches=[batch])
        self.assertEqual(new.report["batch_errors"], [])
        self.assertEqual(new.report["batches_applied"], 1)
        self.assertTrue(any(r["tcp_probe"]["status"] == "reachable" for r in nodes(new)))
        verify_pool(index(new, .1), new.files)


class AttemptMergeTests(unittest.TestCase):
    def setUp(self):
        self.pool = build_pool(source(), at())

    def merge(self, batch, hour, previous=None):
        return build_pool(source("1.1.1.1"), at(hour), previous=(previous or self.pool).files, batches=[batch])

    def probe(self, pool):
        return next(r["tcp_probe"] for r in nodes(pool) if r["ip"] == "8.8.8.8")

    def test_pure_deferral_changes_neither_status_time_nor_failure_streak(self):
        failed = self.merge(report(self.pool, 18), 18)
        batch = report(failed, 24)
        batch["results"][0].update(status="unknown")
        for endpoint in batch["results"][0]["endpoints"]:
            endpoint.update(status="unknown", error="budget")
        before = copy.deepcopy(self.probe(failed))
        after = self.merge(batch, 24, failed)
        self.assertEqual(self.probe(after), before)
        diag = after.report["probe_diagnostics"]
        self.assertEqual(diag["unattempted_nodes"], 1)
        self.assertEqual(diag["attempted_nodes"], 0)
        self.assertEqual(diag["endpoint_error_counts"], {"budget": 1})
        self.assertEqual(diag["socket_attempts"], 1)  # Control only.
        replay = self.merge(batch, 24.1, after)
        self.assertEqual(replay.report["probe_diagnostics"]["reported_nodes"], 0)

    def test_retries_record_attempt_age_without_changing_six_hour_failure_count(self):
        one = self.merge(report(self.pool, 18), 18)
        two = self.merge(report(one, 19), 19, one)
        self.assertEqual(self.probe(two)["consecutive_failures"], 1)
        self.assertEqual(self.probe(two)["last_attempted_at"], at(19))
        success = self.merge(report(two, 20, "reachable"), 20, two)
        later = self.merge(report(success, 21), 21, success)
        self.assertEqual(self.probe(later)["status"], "reachable")
        self.assertEqual(self.probe(later)["checked_at"], at(20))
        self.assertEqual(self.probe(later)["last_attempted_at"], at(21))
        self.assertEqual(self.probe(later)["consecutive_failures"], 0)
        stale = self.merge(report(later, 19), 21.1, later)
        self.assertEqual(self.probe(stale), self.probe(later))

    def test_unknown_retry_advances_attempt_and_collects_cleanup_diagnostics(self):
        batch = report(self.pool, 1)
        batch["results"][0]["status"] = "unknown"
        batch["results"][0]["endpoints"][0].update(status="unknown", error="platform", cleanup_error="close_timeout")
        batch.update(unclosed_sockets=1, recovered_sockets=2, budget_exhausted=True)
        one = self.merge(batch, 1)
        again = copy.deepcopy(batch)
        again.update(batch_id=report(one, 2)["batch_id"], started_at=at(2), finished_at=at(2))
        two = self.merge(again, 2, one)
        self.assertEqual(self.probe(two)["checked_at"], at(2))
        self.assertEqual(self.probe(two)["last_attempt_status"], "unknown")
        self.assertEqual(two.report["probe_diagnostics"]["cleanup_error_counts"], {"close_timeout": 1})
        self.assertEqual(two.report["probe_diagnostics"]["recovered_sockets"], 2)

    def test_task_batches_validate_worker_and_slot_before_applying(self):
        batch = report(self.pool, 18)
        slot = int(parse_utc(at(18)).timestamp()) // 300
        batch.update(schema_version=2, task_slot=slot, task_path=task_path(batch["worker_id"], slot),
                     task_sha256="a" * 64, task_manifest_sha256="b" * 64, scheduled_at=at(18))
        batch.pop("bucket"); batch.pop("bucket_sha256")
        self.assertEqual(self.merge(batch, 18).report["batch_errors"], [])
        batch["worker_id"] = 1 - batch["worker_id"]
        batch["task_path"] = task_path(batch["worker_id"], slot)
        broken = self.merge(batch, 18)
        self.assertTrue(broken.report["batch_errors"])
        self.assertEqual(self.probe(broken)["checked_at"], None)

    def test_diagnostic_summary_uses_existing_batch_counts(self):
        from mirror.__main__ import summary
        pool = self.merge(report(self.pool, 1, "reachable"), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(path)}), redirect_stdout(io.StringIO()):
                summary({"pool": pool.report})
            text = path.read_text(encoding="utf-8")
            self.assertIn("### TCP probe diagnostics", text)
            self.assertIn("| attempted_nodes | 1 |", text)
            self.assertIn("| socket_attempts | 2 |", text)
