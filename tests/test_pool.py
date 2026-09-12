import base64
import copy
import csv
import io
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mirror import MirrorError
from mirror.pool import (build_pool, verify_pool, bucket_id, utc_round, targets, public_ip,
                         verify_catalog, read_pool, assemble, apply_batches)
from mirror.pool_consumer import load_pool, load_pool_config
from mirror.pool_consumer import catalog_directory
from mirror.snapshot import build_snapshot, parse_json, json_bytes
from support import fixture, RUN_URL


def at(hours=0):
    return (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(hours=hours)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def source(only=None, extra=""):
    rows = list(csv.reader(io.StringIO(fixture().decode(), newline="")))
    keys = [x.removeprefix("#") for x in rows[1]]
    output = rows[:2]
    for row in rows[2:]:
        if len(row) <= 1:
            continue
        values = dict(zip(keys, row))
        old = values["IP"]
        ip = "8.8.8.8" if old.endswith(".10") else "1.1.1.1"
        if only and ip != only:
            continue
        config = base64.b64decode(values["OpenVPN_ConfigData_Base64"]).replace(old.encode(), ip.encode()) + extra.encode()
        values.update(IP=ip, OpenVPN_ConfigData_Base64=base64.b64encode(config).decode())
        output.append([values[k] for k in keys])
    output.append(["*"])
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\r\n").writerows(output)
    return build_snapshot(stream.getvalue().encode())


def nodes(pool):
    return parse_json(pool.files["pool/servers.json"])["servers"]


def report(pool, hour, status="unreachable", *, controls=True, identifier=None):
    row = next(r for r in nodes(pool) if r["ip"] == "8.8.8.8")
    control = next(r for r in nodes(pool) if r["ip"] == "1.1.1.1")
    def outcome(row, state):
        return {"id": row["id"], "config_sha256": row["openvpn_config_sha256"], "status": state,
                "endpoints": [{**e, "status": state, "connect_ms": 12 if state == "reachable" else None,
                               "error": None if state == "reachable" else "timeout"} for e in row["probe_targets"]]}
    bucket = bucket_id(row["id"])
    return {"schema_version": 1, "batch_id": identifier or str(uuid.uuid4()), "round": utc_round(at(hour)),
            "worker_id": bucket // 72, "bucket": bucket, "data_commit": "a" * 40,
            "plan_sha256": "b" * 64, "bucket_sha256": "c" * 64,
            "started_at": at(hour), "finished_at": at(hour), "results": [outcome(row, status)],
            "controls": [outcome(control, "reachable")] if controls else []}


def index(pool, hour=0):
    return pool.index("a" * 40, at(hour), at(hour), RUN_URL)


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.first = build_pool(source(), at())

    def test_dedup_observations_and_configuration_reuse(self):
        second = build_pool(source(), at(1), previous=self.first.files)
        self.assertEqual(second.server_count, 2)
        self.assertTrue(second.report["config_bytes_reused"])
        for row in nodes(second):
            self.assertEqual(row["first_seen_at"], at())
            self.assertEqual(row["last_seen_at"], at(1))
            self.assertEqual(self.first.files[row["config"]["path"]], second.files[row["config"]["path"]])
        verify_pool(index(second, 1), second.files)

    def test_absent_node_keeps_real_last_seen_and_expires_at_seven_days(self):
        second = build_pool(source("1.1.1.1"), at(24), previous=self.first.files)
        absent = next(r for r in nodes(second) if r["ip"] == "8.8.8.8")
        self.assertFalse(absent["present_in_latest_source"])
        self.assertEqual(absent["last_seen_at"], at())
        final = build_pool(source("1.1.1.1"), at(168), previous=second.files)
        self.assertEqual(final.server_count, 1)
        self.assertEqual(final.report["expired"], 1)
        self.assertNotIn(absent["config"]["path"], final.files)

    def test_three_consecutive_rounds_and_day_absence_remove(self):
        pool = self.first
        for hour in (18, 24, 30):
            batch = report(pool, hour)
            pool = build_pool(source("1.1.1.1"), at(hour), previous=pool.files, batches=[batch, batch])
            self.assertEqual(pool.server_count, 1 if hour == 30 else 2)
        self.assertEqual(pool.report["tcp_removed"], 1)

    def test_gap_unknown_or_network_guard_prevent_accumulation(self):
        one = build_pool(source("1.1.1.1"), at(18), previous=self.first.files, batches=[report(self.first, 18)])
        gap = build_pool(source("1.1.1.1"), at(30), previous=one.files, batches=[report(one, 30)])
        self.assertEqual(next(r for r in nodes(gap) if r["ip"] == "8.8.8.8")["tcp_probe"]["consecutive_failures"], 1)
        guarded = build_pool(source("1.1.1.1"), at(36), previous=gap.files, batches=[report(gap, 36, controls=False)])
        self.assertEqual(next(r for r in nodes(guarded) if r["ip"] == "8.8.8.8")["tcp_probe"]["consecutive_failures"], 0)

    def test_same_round_success_wins_and_stale_results_do_not_return(self):
        batches = [report(self.first,18),report(self.first,18,"reachable")]
        pool = build_pool(source("1.1.1.1"),at(18),previous=self.first.files,batches=batches)
        row = next(r for r in nodes(pool) if r["ip"] == "8.8.8.8")
        self.assertEqual(row["tcp_probe"]["status"],"reachable")
        self.assertEqual(row["tcp_probe"]["consecutive_failures"],0)
        later = build_pool(source("1.1.1.1"),at(36),previous=pool.files,batches=[report(pool,18)])
        self.assertEqual(next(r for r in nodes(later) if r["ip"] == "8.8.8.8")["tcp_probe"]["status"],"reachable")

    def test_reappearance_and_changed_configuration_reset_failures(self):
        one = build_pool(source("1.1.1.1"),at(18),previous=self.first.files,batches=[report(self.first,18)])
        reappeared = build_pool(source(),at(24),previous=one.files,batches=[report(one,24)])
        self.assertTrue(all(r["tcp_probe"]["consecutive_failures"] == 0 for r in nodes(reappeared)))
        changed = build_pool(source(extra="# new config\n"),at(30),previous=one.files,batches=[report(one,30)])
        self.assertTrue(all(r["tcp_probe"]["round"] is None for r in nodes(changed)))
        self.assertEqual(changed.report["configs_changed"],2)

    def test_tcp_pruning_switch_and_capacity(self):
        pool = self.first
        for hour in (18,24,30):
            pool = build_pool(source("1.1.1.1"),at(hour),previous=pool.files,batches=[report(pool,hour)],tcp_prune=False)
        self.assertEqual(pool.server_count,2)
        with patch("mirror.pool.MAX_SERVERS",1):
            limited = build_pool(source("1.1.1.1"),at(36),previous=pool.files)
        self.assertEqual(limited.report["capacity_evicted"],1)
        self.assertEqual(nodes(limited)[0]["ip"],"1.1.1.1")

    def test_targets_fail_closed_for_unknown_transport_or_address(self):
        config = parse_json(next(b for p,b in self.first.files.items() if p.startswith("pool/configs/")))["openvpn_config_base64"]
        ip = "1.1.1.1" if b"1.1.1.1" in base64.b64decode(config) else "8.8.8.8"
        def alter(old,new):
            return base64.b64encode(base64.b64decode(config).replace(old,new)).decode()
        self.assertEqual(targets(config,ip)[1],"tcp")
        self.assertEqual(targets(alter(b"proto tcp",b"proto udp"),ip)[1],"udp")
        self.assertFalse(targets(alter(ip.encode(),b"example.com"),ip)[0])
        self.assertFalse(targets(base64.b64encode(base64.b64decode(config)+b"\nhttp-proxy example.com 80\n").decode(),ip)[0])
        for addr in ("127.0.0.1","10.0.0.1","169.254.169.254","100.64.0.1","::1","::ffff:127.0.0.1","224.0.0.1"):
            self.assertFalse(public_ip(addr))

    def test_wrong_hash_or_derived_files_cannot_be_verified(self):
        files = dict(self.first.files)
        files["pool/countries.json"] += b"!"
        with self.assertRaises(MirrorError): verify_pool(index(self.first),files)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,"pool").mkdir()
            Path(directory,"pool","servers.json").write_bytes(b"{}")
            with self.assertRaises(MirrorError): read_pool(directory)

    def test_bad_batch_never_acknowledged_or_counted(self):
        bad = report(self.first,24)
        bad["results"][0]["endpoints"][0]["ip"] = "127.0.0.1"
        pool = build_pool(source("1.1.1.1"),at(24),previous=self.first.files,batches=[bad])
        self.assertTrue(pool.report["batch_errors"])
        self.assertNotIn(bad["batch_id"],parse_json(pool.files["pool/state.json"])["processed_batches"])

    def test_empty_pool_catalog_is_valid(self):
        empty = assemble({}, {}, {}, at())
        verify_pool(index(empty),empty.files)
        self.assertEqual(verify_catalog(index(empty),empty.files),[])

    def test_consumer_lazy_config_fallback_and_old_cache(self):
        manifest = index(self.first)
        calls=[]
        def read(url,limit):
            calls.append(url)
            if url.endswith("/pool/latest.json"): return json_bytes(manifest)
            if "cdn.jsdelivr.net" in url: raise TimeoutError()
            return next(body for path,body in self.first.files.items() if url.endswith("/"+path))
        with tempfile.TemporaryDirectory() as directory:
            got, rows = load_pool("a/b",cache=directory,read=read)
            self.assertFalse(any("/configs/" in c for c in calls))
            decoded = load_pool_config("a/b",got,rows[0],cache=directory,read=read)
            self.assertEqual(len(decoded),rows[0]["openvpn_config_bytes"])
            pointer=Path(directory,"current.json").read_bytes()
            manifest["source_fetched_at"]=at(-1)
            with self.assertRaises(MirrorError): load_pool("a/b",cache=directory,read=read)
            self.assertEqual(Path(directory,"current.json").read_bytes(),pointer)

    def test_24_hour_boundary_requires_new_failure_and_source_reset_wins(self):
        pool = self.first
        for hour in (6,12,18):
            pool = build_pool(source("1.1.1.1"), at(hour), previous=pool.files, batches=[report(pool,hour)])
        self.assertEqual(pool.server_count,2)  # Three failures before 24h cannot prune.
        without_result=build_pool(source("1.1.1.1"),at(24),previous=pool.files)
        self.assertEqual(without_result.server_count,2)  # Missing a batch is not a failure.
        reappeared=build_pool(source(),at(23),previous=pool.files)
        old=report(pool,18)
        later=build_pool(source("1.1.1.1"),at(24),previous=reappeared.files,batches=[old])
        self.assertEqual(next(r for r in nodes(later) if r["ip"]=="8.8.8.8")["tcp_probe"]["consecutive_failures"],0)
        removed=build_pool(source("1.1.1.1"),at(24),previous=pool.files,batches=[report(pool,24)])
        self.assertEqual(removed.report["tcp_removed"],1)

    def test_multiremote_failure_requires_every_endpoint_and_overrides_are_explicit(self):
        row=next(r for r in nodes(self.first) if r["ip"]=="8.8.8.8")
        config=parse_json(self.first.files[row["config"]["path"]])["openvpn_config_base64"]
        raw=base64.b64decode(config)
        encode=lambda body:base64.b64encode(body).decode()
        endpoints,reason=targets(encode(raw+b"\nremote 8.8.8.8 444 tcp-client\n"),row["ip"])
        self.assertEqual(reason,"tcp");self.assertEqual(len(endpoints),2)
        self.assertFalse(targets(encode(raw+b"\nremote 8.8.8.8 444 udp\n"),row["ip"])[0])
        self.assertFalse(targets(encode(raw+b"\nconfig external.ovpn\n"),row["ip"])[0])
        self.assertFalse(targets(encode(raw+b"\n<connection>\nremote 8.8.8.8 444\n</connection>\n"),row["ip"])[0])
        profiles=b"\n".join(line for line in raw.splitlines() if not line.startswith(b"remote "))
        profiles+=b"\n<connection>\nremote 8.8.8.8 443\n</connection>\n<connection>\nremote 8.8.8.8 444\n</connection>\n"
        self.assertEqual(targets(encode(profiles),row["ip"])[1],"complex_configuration")
        from mirror.pool import valid_result
        result={"config_sha256":row["openvpn_config_sha256"],"status":"unknown","endpoints":[
          {**endpoints[0],"status":"unreachable","error":"timeout","connect_ms":None},
          {**endpoints[1],"status":"unknown","error":"budget","connect_ms":None}]}
        self.assertEqual(valid_result(result,{**row,"probe_targets":endpoints}),"unknown")

    def test_batch_failure_rate_guard_includes_old_configuration_results(self):
        batch=report(self.first,24)
        for number in range(9):
            extra=copy.deepcopy(batch["results"][0]);extra["id"]="v1:"+f"{number:064x}"
            batch["results"].append(extra)
        pool=build_pool(source("1.1.1.1"),at(24),previous=self.first.files,batches=[batch])
        self.assertEqual(pool.report["guarded_batches"],1)
        self.assertEqual(next(r for r in nodes(pool) if r["ip"]=="8.8.8.8")["tcp_probe"]["consecutive_failures"],0)

    def test_config_capacity_evicts_old_records_and_not_current_source(self):
        current=next(r for r in nodes(self.first) if r["ip"]=="1.1.1.1")
        with patch("mirror.pool.MAX_CONFIG_TOTAL",current["config"]["bytes"]):
            pool=build_pool(source("1.1.1.1"),at(24),previous=self.first.files)
            self.assertEqual(pool.report["capacity_evicted"],1)
            with self.assertRaises(MirrorError): build_pool(source(),at(24),previous=self.first.files)

    def test_schema_documents_and_shared_fixture_match_produced_protocol(self):
        root=Path(__file__).resolve().parents[1]/"schemas/pool/v1"
        instances={"latest":index(self.first),"servers":parse_json(self.first.files["pool/servers.json"]),
          "countries":parse_json(self.first.files["pool/countries.json"]),
          "probe-plan":parse_json(self.first.files["pool/probe-plan.json"]),
          "state":parse_json(self.first.files["pool/state.json"]),
          "bucket":parse_json(self.first.files["pool/probes/000.json"]),
          "config":parse_json(next(b for p,b in self.first.files.items() if p.startswith("pool/configs/")))}
        for name,instance in instances.items():
            schema=parse_json((root/f"{name}.schema.json").read_bytes())
            self.assertTrue(set(schema["required"]).issubset(instance),name)
            self.assertEqual(schema["properties"]["schema_version"]["const"],1)
        golden=parse_json(Path(__file__).with_name("fixtures").joinpath("pool.json").read_bytes())
        self.assertEqual(golden["index"],index(self.first))
        for path,body in golden["files"].items():
            self.assertEqual(base64.b64decode(body),self.first.files[path])

    def test_partial_existing_cache_or_failed_pointer_cannot_replace_success(self):
        candidate=self.first
        manifest=index(candidate)
        def read(url,limit):
            if url.endswith("latest.json"):return json_bytes(manifest)
            return next(body for path,body in candidate.files.items() if url.endswith("/"+path))
        with tempfile.TemporaryDirectory() as directory:
            load_pool("a/b",cache=directory,read=read)
            before=Path(directory,"current.json").read_bytes()
            candidate=build_pool(source(),at(1),previous=candidate.files)
            manifest=index(candidate,1)
            target=catalog_directory(directory,manifest)/"pool/servers.json"
            target.parent.mkdir(parents=True);target.write_bytes(b"partial")
            with self.assertRaises(MirrorError):load_pool("a/b",cache=directory,read=read)
            self.assertEqual(before,Path(directory,"current.json").read_bytes())
            # A completed candidate may remain, but a failed pointer write must
            # still leave the old success discoverable.
            for path in ("pool/servers.json","pool/countries.json"):
                (catalog_directory(directory,manifest)/path).write_bytes(candidate.files[path])
            with patch("mirror.pool_consumer.atomic_bytes",side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):load_pool("a/b",cache=directory,read=read)
            self.assertEqual(before,Path(directory,"current.json").read_bytes())
