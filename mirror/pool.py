"""Rolling observations and verified Worker results. This module never probes nodes."""

import copy
import ipaddress
import re
import shlex
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from . import MAX_FILE_BYTES, MAX_SERVERS, SOURCE_URL, MirrorError
from .snapshot import json_bytes, parse_json
from .validate import COMMIT, SHA256, decode_config, integer, node_id, parse_utc, sha256

CATALOG_PATHS = ("pool/servers.json", "pool/countries.json")
POOL_PATHS = (*CATALOG_PATHS, "pool/probe-plan.json", "pool/state.json")
POOL_INDEX = "pool/latest.json"
MAX_INDEX = 65536
MAX_CONFIG_TOTAL = 64 * 1024 * 1024
MAX_CONFIG_FILE = 176000
BUCKETS = 144
ROUND_SECONDS = 21600
KINDS = {"reachable", "unreachable", "unknown", "not_applicable"}
NODE = re.compile(r"v1:[0-9a-f]{64}\Z")
BATCH = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
PUBLIC_FIELDS = ("id", "hostname", "ip", "country_code", "country_name", "score", "ping_ms",
                 "speed_bps", "num_vpn_sessions", "openvpn_config_sha256", "openvpn_config_bytes")


def require(condition, message):
    if not condition:
        raise MirrorError(message)


def digest(value):
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def utc_round(value):
    return int(parse_utc(value).timestamp()) // ROUND_SECONDS


def bucket_id(identifier):
    return int(identifier[3:11], 16) % BUCKETS


def public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        return (ip.is_global and not (ip.is_multicast or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_unspecified) and "%" not in value
                and ip.ipv4_mapped is None) if ip.version == 6 else (
                    ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified))
    except ValueError:
        return False


def targets(encoded, ip):
    """Deliberately conservative extraction; unsupported semantics stay downloadable."""
    if not public_ip(ip):
        return [], "non_public_ip"
    text = decode_config(encoded).decode("utf-8")
    proto, remotes, block = None, [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if block:
            if line == f"</{block}>":
                block = None
            continue
        if line.startswith("<"):
            if line == "<connection>":
                return [], "complex_configuration"
            block = line[1:-1]
            continue
        key = line.split(None, 1)[0].removeprefix("--")
        if key in {"config", "connection", "http-proxy", "http-proxy-option", "socks-proxy",
                   "port", "rport", "remote-random-hostname"}:
            return [], "complex_configuration"
        if key not in {"proto", "remote"}:
            continue
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            return [], "complex_configuration"
        if key == "proto":
            if len(parts) != 2 or proto is not None:
                return [], "ambiguous_protocol"
            proto = parts[1]
        else:
            remotes.append(parts)
    endpoints, protocols = set(), []
    for parts in remotes:
        if len(parts) not in {3, 4} or parts[1] != ip or not parts[2].isdigit():
            return [], "ambiguous_endpoint"
        port = int(parts[2])
        if not 1 <= port <= 65535:
            return [], "ambiguous_endpoint"
        transport = parts[3] if len(parts) == 4 else proto
        protocols.append(transport)
        if transport not in {"tcp", "tcp4", "tcp6", "tcp-client", "tcp4-client", "tcp6-client"}:
            continue
        if ("4" in transport and ":" in ip) or ("6" in transport and ":" not in ip):
            return [], "ambiguous_protocol"
        endpoints.add((ip, port))
    if protocols and all(p in {"udp", "udp4", "udp6"} for p in protocols):
        return [], "udp"
    if not protocols or any(p not in {"tcp", "tcp4", "tcp6", "tcp-client", "tcp4-client", "tcp6-client"}
                            for p in protocols) or not 1 <= len(endpoints) <= 8:
        return [], "ambiguous_protocol"
    return [{"ip": addr, "port": port} for addr, port in sorted(endpoints)], "tcp"


def fresh_probe(reason):
    return {"status": "not_applicable" if reason == "udp" else "unknown",
            "probe_source": "cloudflare_workers", "checked_at": None, "last_success_at": None,
            "round": None, "worker_id": None, "consecutive_failures": 0,
            "last_failure_round": None, "connect_ms": None, "connected_endpoint": None}


def descriptor(body):
    return {"sha256": sha256(body), "bytes": len(body)}


def config_file(server):
    body = json_bytes({"schema_version": 1, "kind": "vpngate-pool-config",
                       **{key: server[key] for key in ("openvpn_config_base64", "openvpn_config_sha256",
                                                        "openvpn_config_bytes")}})
    path = f"pool/configs/{server['openvpn_config_sha256']}.json"
    return path, body


def observed(server, at, old=None):
    endpoints, reason = targets(server["openvpn_config_base64"], server["ip"])
    path, body = config_file(server)
    same = old and old["openvpn_config_sha256"] == server["openvpn_config_sha256"]
    probe = copy.deepcopy(old["tcp_probe"]) if same else fresh_probe(reason)
    probe.update(consecutive_failures=0, last_failure_round=None)
    return {**{key: server[key] for key in PUBLIC_FIELDS}, "first_seen_at": old["first_seen_at"] if old else at,
            "last_seen_at": at, "present_in_latest_source": True,
            "config": {"path": path, **descriptor(body)}, "probe_targets": endpoints,
            "probe_reason": reason, "tcp_probe": probe}, path, body


def countries(nodes):
    groups = {}
    for row in nodes:
        entry = groups.setdefault(row["country_code"], {"names": set(), "server_count": 0})
        entry["server_count"] += 1
        if row["country_name"]:
            entry["names"].add(row["country_name"])
    return [{"code": code, "names": sorted(value["names"]), "server_count": value["server_count"]}
            for code, value in sorted(groups.items(), key=lambda item: (item[0] is None, item[0] or ""))]


def target_record(row):
    return {"id": row["id"], "config_sha256": row["openvpn_config_sha256"], "ip": row["ip"],
            "endpoints": row["probe_targets"], "last_seen_at": row["last_seen_at"],
            "last_probe_round": row["tcp_probe"]["round"], "checked_at": row["tcp_probe"]["checked_at"]}


@dataclass
class PoolSnapshot:
    files: dict
    source_fetched_at: str
    server_count: int
    report: dict

    def index(self, commit, generated_at, index_at, run_url):
        return {"kind": "vpngate-pool", "schema_version": 1, "source_url": SOURCE_URL,
                "source_fetched_at": self.source_fetched_at, "generated_at": generated_at,
                "index_generated_at": index_at, "data_commit": commit, "server_count": self.server_count,
                "files": {path: descriptor(self.files[path]) for path in POOL_PATHS}, "workflow_run_url": run_url}


def assemble(nodes, configs, processed, fetched_at, report=None, *, task_files=None, legacy=False):
    rows = sorted(nodes.values(), key=lambda row: row["id"])
    common = {"kind": "vpngate-pool", "schema_version": 1, "source_fetched_at": fetched_at,
              "server_count": len(rows)}
    files = {"pool/servers.json": json_bytes({**common, "servers": rows}),
             "pool/countries.json": json_bytes({**common, "countries": countries(rows)}),
             "pool/state.json": json_bytes({"schema_version": 1, "processed_batches": processed})}
    for row in rows:
        files[row["config"]["path"]] = configs[row["config"]["path"]]
    buckets = {}
    for number in range(BUCKETS):
        bucket = [target_record(row) for row in rows if row["probe_targets"] and bucket_id(row["id"]) == number]
        bucket.sort(key=lambda row: (row["checked_at"] or "", row["id"]))
        path = f"pool/probes/{number:03}.json"
        files[path] = json_bytes({"schema_version": 1, "bucket": number, "targets": bucket})
        require(len(files[path]) <= 262144, "Probe bucket exceeds Worker input limit")
        buckets[str(number)] = {"path": path, **descriptor(files[path])}
    controls = [target_record(row) for row in rows
                if row["present_in_latest_source"] and len(row["probe_targets"]) == 1][:5]
    plan = {"schema_version": 1, "bucket_count": BUCKETS,
            "source_fetched_at": fetched_at, "controls": controls, "buckets": buckets}
    if not legacy:
        from .probe_plan import attach_tasks, build_tasks
        attach_tasks(files, plan, build_tasks(rows, fetched_at) if task_files is None else task_files)
    files["pool/probe-plan.json"] = json_bytes(plan)
    require(all(len(body) <= MAX_FILE_BYTES for body in files.values()), "Pool file exceeds size limit")
    require(len(files["pool/probe-plan.json"]) <= MAX_INDEX, "Probe manifest exceeds size limit")
    return PoolSnapshot(files, fetched_at, len(rows), report or {})


def valid_result(result, row):
    require(isinstance(result, dict), "Invalid probe result")
    require(result.get("config_sha256") == row["openvpn_config_sha256"], "Old probe configuration")
    endpoint_results = result.get("endpoints")
    require(isinstance(endpoint_results, list) and 1 <= len(endpoint_results) <= 8, "Invalid endpoint results")
    endpoints = [{"ip": e.get("ip"), "port": e.get("port")} for e in endpoint_results if isinstance(e, dict)]
    require(endpoints == row["probe_targets"], "Probe endpoints do not match configuration")
    for value in endpoint_results:
        require(value.get("status") in {"reachable", "unreachable", "unknown"}, "Invalid endpoint status")
        require(value.get("error") in {None, "timeout", "refused", "platform", "unsupported", "budget"}, "Invalid error class")
        if value["status"] == "unreachable":
            require(value["error"] in {"timeout", "refused"}, "Platform errors cannot count as failures")
        if value["status"] == "reachable":
            integer(value.get("connect_ms"), 0, 3000, "Connection duration")
            require(value["error"] is None, "Successful endpoint has an error")
        else:
            require(value.get("connect_ms") is None, "Non-successful endpoint has a duration")
        if value["status"] == "unknown":
            require(value["error"] in {"platform", "unsupported", "budget"}, "Invalid unknown result")
    status = ("reachable" if any(e["status"] == "reachable" for e in endpoint_results) else
              "unreachable" if all(e["status"] == "unreachable" for e in endpoint_results) else "unknown")
    require(result.get("status") == status, "Inconsistent probe result")
    return status


def apply_batches(nodes, batches, processed, now, prune):
    report = {"batches_applied": 0, "batches_ignored": 0, "batch_errors": [], "tcp_removed": 0,
              "guarded_batches": 0, "deferred": 0,
              "probe_diagnostics": {"reported_nodes": 0, "attempted_nodes": 0, "definitive_nodes": 0,
                  "unattempted_nodes": 0, "endpoint_error_counts": {}, "cleanup_error_counts": {},
                  "processed_endpoints": 0, "socket_attempts": 0, "unclosed_sockets": 0,
                  "recovered_sockets": 0, "budget_exhausted_batches": 0, "socket_stopped_batches": 0}}
    current_round, fresh_failures = utc_round(now), set()
    instant = parse_utc(now)
    for batch in sorted(batches, key=lambda b: (str(b.get("finished_at", "")), str(b.get("batch_id", ""))) if isinstance(b, dict) else ("", "")):
        try:
            identifier = batch.get("batch_id")
            require(isinstance(identifier, str) and BATCH.fullmatch(identifier), "Invalid batch ID")
            if identifier in processed:
                continue
            version = batch.get("schema_version")
            require(type(version) is int and version in (1, 2), "Unsupported batch schema")
            round_number = batch.get("round")
            integer(round_number, 0, 10**9, "Probe round")
            integer(batch.get("worker_id"), 0, 1, "Worker ID")
            if version == 1:
                integer(batch.get("bucket"), 0, 143, "Probe bucket")
                require(batch["bucket"] // 72 == batch["worker_id"], "Wrong worker partition")
                require(digest(batch.get("bucket_sha256")), "Invalid probe bucket hash")
            else:
                from .probe_plan import task_path
                integer(batch.get("task_slot"), 0, 10**10, "Task slot")
                require(batch.get("task_path") == task_path(batch["worker_id"], batch["task_slot"])
                        and digest(batch.get("task_sha256")) and digest(batch.get("task_manifest_sha256")), "Invalid task reference")
                require(int(parse_utc(batch["scheduled_at"]).timestamp()) // 300 == batch["task_slot"], "Wrong task slot")
            require(isinstance(batch.get("data_commit"), str) and COMMIT.fullmatch(batch["data_commit"]), "Invalid probe commit")
            require(digest(batch.get("plan_sha256")), "Invalid probe manifest hash")
            start, end = parse_utc(batch["started_at"]), parse_utc(batch["finished_at"])
            require(start <= end <= instant + timedelta(minutes=5) and end - start <= timedelta(minutes=2), "Invalid batch times")
            require(0 <= start.timestamp() - round_number * ROUND_SECONDS <= ROUND_SECONDS + 120, "Wrong batch round")
            if version == 2:
                require(-300 <= (start - parse_utc(batch["scheduled_at"])).total_seconds() <= 10800, "Invalid task execution delay")
            results, controls = batch.get("results"), batch.get("controls")
            require(isinstance(results, list) and isinstance(controls, list) and len(results) <= 40 and len(controls) <= 5, "Invalid batch arrays")
            unique_endpoints = {}
            for result in results + controls:
                require(isinstance(result, dict) and isinstance(result.get("id"), str) and NODE.fullmatch(result["id"])
                        and digest(result.get("config_sha256")), "Invalid result identity")
                endpoint_results = result.get("endpoints")
                require(isinstance(endpoint_results, list) and 1 <= len(endpoint_results) <= 8, "Invalid endpoints")
                endpoints = []
                for endpoint in endpoint_results:
                    require(isinstance(endpoint, dict) and public_ip(endpoint.get("ip")), "Invalid public endpoint")
                    integer(endpoint.get("port"), 1, 65535, "Endpoint port")
                    key = (endpoint["ip"], endpoint["port"])
                    require(key not in unique_endpoints or unique_endpoints[key] == endpoint, "Conflicting endpoint results")
                    unique_endpoints[key] = endpoint
                    endpoints.append({"ip": key[0], "port": key[1]})
                require(len({(e["ip"], e["port"]) for e in endpoints}) == len(endpoints), "Duplicate endpoint")
                valid_result(result, {"openvpn_config_sha256": result["config_sha256"], "probe_targets": endpoints})
            require(len(unique_endpoints) <= 40 and all(len(r["endpoints"]) == 1 for r in controls), "Oversized probe batch")
            require(len({r.get("id") for r in results}) == len(results), "Duplicate probe node")
            integer(batch.get("deferred", 0), 0, MAX_SERVERS, "Deferred count")
            integer(batch.get("unclosed_sockets", 0), 0, 4, "Unclosed sockets")
            integer(batch.get("recovered_sockets", 0), 0, 40, "Recovered sockets")
            require(type(batch.get("budget_exhausted", False)) is bool, "Invalid budget diagnostic")
            require(batch.get("stop_reason") in (None, "socket_close_unconfirmed"), "Invalid stop reason")
            for endpoint in unique_endpoints.values():
                require(endpoint.get("cleanup_error") in (None, "close_timeout", "close_rejected"), "Invalid cleanup diagnostic")
            if round_number not in {current_round, current_round - 1} or instant - end > timedelta(hours=12):
                report["batches_ignored"] += 1
                continue
            usable = []
            control_success = False
            for result, control in [(r, False) for r in results] + [(r, True) for r in controls]:
                row = nodes.get(result.get("id"))
                if not row or row["openvpn_config_sha256"] != result.get("config_sha256"):
                    continue
                if not control:
                    require(bucket_id(row["id"]) // 72 == batch["worker_id"], "Result in wrong worker partition")
                    if version == 1:
                        require(bucket_id(row["id"]) == batch["bucket"], "Result in wrong bucket")
                status = valid_result(result, row)
                if control:
                    control_success |= status == "reachable"
                else:
                    usable.append((row, result, status))
            complete = [result["status"] for result in results if result["status"] != "unknown"]
            guarded = not control_success or (len(complete) >= 10 and complete.count("unreachable") / len(complete) >= 0.8)
            report["guarded_batches"] += guarded
            report["deferred"] += batch.get("deferred", 0)
            diagnostics = report["probe_diagnostics"]
            diagnostics["reported_nodes"] += len(results)
            diagnostics["definitive_nodes"] += len(complete)
            unattempted = sum(all(e["error"] == "budget" for e in r["endpoints"]) for r in results)
            diagnostics["unattempted_nodes"] += unattempted
            diagnostics["attempted_nodes"] += len(results) - unattempted
            diagnostics["processed_endpoints"] += sum(e["error"] != "budget" for e in unique_endpoints.values())
            diagnostics["socket_attempts"] += sum(e["error"] not in ("budget", "unsupported") for e in unique_endpoints.values())
            diagnostics["unclosed_sockets"] += batch.get("unclosed_sockets", 0)
            diagnostics["recovered_sockets"] += batch.get("recovered_sockets", 0)
            diagnostics["budget_exhausted_batches"] += batch.get("budget_exhausted", False)
            diagnostics["socket_stopped_batches"] += batch.get("stop_reason") == "socket_close_unconfirmed"
            for endpoint in unique_endpoints.values():
                for field, key in (("error", "endpoint_error_counts"), ("cleanup_error", "cleanup_error_counts")):
                    value = endpoint.get(field)
                    if value:
                        diagnostics[key][value] = diagnostics[key].get(value, 0) + 1
            for row, result, status in usable:
                probe = row["tcp_probe"]
                # Pure deferral is not an observation and cannot consume a round.
                if all(e["error"] == "budget" for e in result["endpoints"]):
                    continue
                previous_attempt = probe.get("last_attempted_at") or probe["checked_at"]
                if previous_attempt and parse_utc(previous_attempt) > end:
                    continue
                old_round = probe["round"]
                if old_round is not None and round_number < old_round:
                    continue
                probe.update(last_attempted_at=batch["finished_at"], last_attempt_status=status)
                if old_round == round_number and ((probe["status"] == "reachable" and status != "reachable")
                                                  or (probe["status"] != "unknown" and status == "unknown")):
                    continue
                counted = probe["last_failure_round"]
                can_fail = (status == "unreachable" and not guarded and not row["present_in_latest_source"]
                            and parse_utc(row["last_seen_at"]) <= start)
                if status == "reachable":
                    probe.update(consecutive_failures=0, last_failure_round=None, last_success_at=batch["finished_at"])
                    fresh_failures.discard(row["id"])
                elif can_fail:
                    if counted != round_number:
                        probe["consecutive_failures"] = probe["consecutive_failures"] + 1 if counted == round_number - 1 else 1
                    probe["last_failure_round"] = round_number
                    fresh_failures.add(row["id"])
                else:
                    probe.update(consecutive_failures=0, last_failure_round=None)
                    fresh_failures.discard(row["id"])
                connected = next((e for e in result["endpoints"] if e["status"] == "reachable"), None)
                probe.update(status=status, round=round_number, worker_id=batch["worker_id"], checked_at=batch["finished_at"],
                             connect_ms=connected["connect_ms"] if connected else None,
                             connected_endpoint={"ip": connected["ip"], "port": connected["port"]} if connected else None)
            processed[identifier] = round_number
            report["batches_applied"] += 1
        except (MirrorError, KeyError, TypeError, ValueError, AttributeError) as exc:
            report["batch_errors"].append(str(exc)[:160])
    for identifier in fresh_failures:
        row = nodes[identifier]
        if prune and row["tcp_probe"]["consecutive_failures"] >= 3 and instant - parse_utc(row["last_seen_at"]) >= timedelta(hours=24):
            del nodes[identifier]
            report["tcp_removed"] += 1
    report["batch_errors"] = report["batch_errors"][:10]
    return report


def build_pool(snapshot, fetched_at, *, previous=None, seed=None, batches=(), tcp_prune=True):
    nodes, configs, processed = {}, {}, {}
    if previous:
        nodes = {r["id"]: copy.deepcopy(r) for r in parse_json(previous["pool/servers.json"])["servers"]}
        configs = {p: b for p, b in previous.items() if p.startswith("pool/configs/")}
        processed = dict(parse_json(previous["pool/state.json"])["processed_batches"])
    elif seed:
        seed_index, seed_files = seed
        for server in parse_json(seed_files["data/servers.json"])["servers"]:
            row, path, body = observed(server, seed_index["fetched_at"])
            nodes[row["id"]], configs[path] = row, body
    for row in nodes.values():
        row["present_in_latest_source"] = False
    added, changed, reused = 0, 0, 0
    for server in parse_json(snapshot.files["data/servers.json"])["servers"]:
        old = nodes.get(server["id"])
        added += old is None
        changed += bool(old and old["openvpn_config_sha256"] != server["openvpn_config_sha256"])
        row, path, body = observed(server, fetched_at, old)
        if configs.get(path) == body:
            reused += len(body)
        nodes[row["id"]], configs[path] = row, body
    result = apply_batches(nodes, batches, processed, fetched_at, tcp_prune)
    expired = [key for key, row in nodes.items() if parse_utc(fetched_at) - parse_utc(row["last_seen_at"]) >= timedelta(days=7)]
    for key in expired:
        del nodes[key]
    def fits():
        paths = {r["config"]["path"] for r in nodes.values()}
        return len(nodes) <= MAX_SERVERS and sum(len(configs[p]) for p in paths) <= MAX_CONFIG_TOTAL
    evicted = 0
    for row in sorted((r for r in nodes.values() if not r["present_in_latest_source"]), key=lambda r: (r["last_seen_at"], r["id"])):
        if fits():
            break
        del nodes[row["id"]]
        evicted += 1
    require(fits(), "Current source exceeds pool capacity")
    processed = {key: value for key, value in processed.items() if value >= utc_round(fetched_at) - 1}
    result.update(added=added, configs_changed=changed, config_bytes_reused=reused,
                  expired=len(expired), capacity_evicted=evicted, server_count=len(nodes),
                  tcp_prune_enabled=tcp_prune,
                  never_probed=sum(r["tcp_probe"]["checked_at"] is None for r in nodes.values()),
                  oldest_probe_age_hours=max((round(max(0, (parse_utc(fetched_at) - parse_utc(r["tcp_probe"]["checked_at"])).total_seconds()) / 3600, 2)
                                              for r in nodes.values() if r["tcp_probe"]["checked_at"]), default=None))
    from .probe_plan import build_tasks
    task_files = build_tasks(list(nodes.values()), fetched_at, previous)
    return assemble(nodes, configs, processed, fetched_at, result, task_files=task_files)


def validate_pool_index(index):
    require(isinstance(index, dict) and index.get("kind") == "vpngate-pool" and type(index.get("schema_version")) is int
            and index["schema_version"] == 1, "Unsupported pool schema")
    require(index.get("source_url") == SOURCE_URL, "Wrong pool source")
    require(isinstance(index.get("data_commit"), str) and COMMIT.fullmatch(index["data_commit"]), "Invalid pool commit")
    times = [parse_utc(index.get(key)) for key in ("source_fetched_at", "generated_at", "index_generated_at")]
    require(times[2] >= max(times[:2]), "Invalid pool times")
    integer(index.get("server_count"), 0, MAX_SERVERS, "Pool count")
    require(isinstance(index.get("files"), dict) and set(index["files"]) == set(POOL_PATHS), "Invalid pool manifest")
    for path, meta in index["files"].items():
        require(isinstance(meta, dict) and digest(meta.get("sha256")), "Invalid pool file hash")
        integer(meta.get("bytes"), 1, MAX_INDEX if path == "pool/probe-plan.json" else MAX_FILE_BYTES, "Pool file size")
    require(len(json_bytes(index)) <= MAX_INDEX, "Oversized pool index")
    return index


def check_bytes(body, meta):
    require(isinstance(body, bytes) and len(body) == meta["bytes"] and sha256(body) == meta["sha256"], "Pool integrity mismatch")


def verify_catalog(index, files):
    validate_pool_index(index)
    for path in CATALOG_PATHS:
        check_bytes(files.get(path), index["files"][path])
    catalog = parse_json(files[CATALOG_PATHS[0]])
    groups = parse_json(files[CATALOG_PATHS[1]])
    for document in (catalog, groups):
        require(document.get("kind") == "vpngate-pool" and type(document.get("schema_version")) is int and document["schema_version"] == 1
                and document.get("server_count") == index["server_count"]
                and document.get("source_fetched_at") == index["source_fetched_at"], "Mixed pool catalog")
    rows = catalog.get("servers")
    require(isinstance(rows, list) and len(rows) == index["server_count"], "Wrong pool count")
    ids, config_sizes = [], {}
    for row in rows:
        require(row["id"] == node_id(row["hostname"], row["ip"]), "Invalid pool node ID")
        require(row["country_code"] is None or re.fullmatch(r"[A-Z]{2}", row["country_code"]), "Invalid country code")
        require(row["country_name"] is None or isinstance(row["country_name"], str), "Invalid country name")
        for key in ("score", "ping_ms", "speed_bps", "num_vpn_sessions"):
            if row[key] is not None:
                integer(row[key], 0, 2**53 - 1, key)
        require(parse_utc(row["first_seen_at"]) <= parse_utc(row["last_seen_at"]) <= parse_utc(index["source_fetched_at"]), "Invalid observation times")
        require(type(row["present_in_latest_source"]) is bool, "Invalid presence flag")
        require(not row["present_in_latest_source"] or row["last_seen_at"] == index["source_fetched_at"], "Wrong current observation time")
        require(digest(row["openvpn_config_sha256"]), "Invalid config content hash")
        integer(row["openvpn_config_bytes"], 1, 131072, "Config content size")
        meta = row["config"]
        require(meta["path"] == f"pool/configs/{row['openvpn_config_sha256']}.json" and digest(meta["sha256"]), "Invalid config reference")
        integer(meta["bytes"], 1, MAX_CONFIG_FILE, "Config file size")
        require(meta["path"] not in config_sizes or config_sizes[meta["path"]] == meta, "Conflicting config references")
        config_sizes[meta["path"]] = meta
        probe = row["tcp_probe"]
        require(probe["status"] in KINDS and probe["probe_source"] == "cloudflare_workers", "Invalid probe state")
        integer(probe["consecutive_failures"], 0, 10000, "Failure count")
        for key in ("round", "last_failure_round", "worker_id", "connect_ms"):
            if probe[key] is not None:
                integer(probe[key], 0, 1 if key == "worker_id" else 3000 if key == "connect_ms" else 10**9, key)
        for key in ("checked_at", "last_success_at"):
            if probe[key] is not None:
                require(parse_utc(probe[key]) <= parse_utc(index["index_generated_at"]) + timedelta(minutes=5), "Future probe time")
        require(probe["last_failure_round"] is not None or probe["consecutive_failures"] == 0, "Inconsistent failure state")
        require((probe["checked_at"] is None) == (probe["round"] is None) == (probe["worker_id"] is None), "Incomplete probe identity")
        require(probe["round"] is None or utc_round(probe["checked_at"]) in {probe["round"], probe["round"] + 1}, "Invalid probe round")
        require(probe["last_success_at"] is None or (probe["checked_at"] is not None and parse_utc(probe["last_success_at"]) <= parse_utc(probe["checked_at"])), "Invalid success time")
        if "last_attempted_at" in probe:
            require(probe.get("last_attempt_status") in {"reachable", "unreachable", "unknown"}, "Invalid attempt status")
            attempt_at = parse_utc(probe["last_attempted_at"])
            require(attempt_at <= parse_utc(index["index_generated_at"]) + timedelta(minutes=5)
                    and (probe["checked_at"] is None or parse_utc(probe["checked_at"]) <= attempt_at), "Invalid attempt time")
        require(not probe["consecutive_failures"] or (probe["last_failure_round"] == probe["round"] and probe["status"] == "unreachable"), "Invalid failure round")
        require(isinstance(row["probe_targets"], list) and len(row["probe_targets"]) <= 8, "Invalid targets")
        for endpoint in row["probe_targets"]:
            require(endpoint["ip"] == row["ip"] and public_ip(endpoint["ip"]), "Non-public probe target")
            integer(endpoint["port"], 1, 65535, "Target port")
        require((probe["status"] == "reachable") == (probe["connected_endpoint"] is not None and probe["connect_ms"] is not None), "Invalid connected endpoint")
        require(probe["connected_endpoint"] is None or probe["connected_endpoint"] in row["probe_targets"], "Unlisted connected endpoint")
        ids.append(row["id"])
    require(ids == sorted(set(ids)), "Unsorted or duplicate pool IDs")
    require(groups.get("countries") == countries(rows), "Pool country mismatch")
    require(sum(m["bytes"] for m in config_sizes.values()) <= MAX_CONFIG_TOTAL, "Pool configuration capacity exceeded")
    return rows


def verify_config(row, body):
    check_bytes(body, row["config"])
    config = parse_json(body)
    require(config.get("kind") == "vpngate-pool-config" and type(config.get("schema_version")) is int and config["schema_version"] == 1, "Unsupported config schema")
    decoded = decode_config(config["openvpn_config_base64"])
    require(sha256(decoded) == row["openvpn_config_sha256"] == config["openvpn_config_sha256"]
            and len(decoded) == row["openvpn_config_bytes"] == config["openvpn_config_bytes"], "Config content mismatch")
    return decoded


def verify_pool(index, files):
    rows = verify_catalog(index, files)
    for path in POOL_PATHS:
        check_bytes(files.get(path), index["files"][path])
    for row in rows:
        body = files.get(row["config"]["path"])
        verify_config(row, body)
        endpoints, reason = targets(parse_json(body)["openvpn_config_base64"], row["ip"])
        require((row["probe_targets"], row["probe_reason"]) == (endpoints, reason), "Wrong derived probe targets")
    state = parse_json(files["pool/state.json"])
    require(type(state.get("schema_version")) is int and state["schema_version"] == 1 and isinstance(state.get("processed_batches"), dict), "Invalid pool state")
    for key, value in state["processed_batches"].items():
        require(BATCH.fullmatch(key) is not None, "Invalid processed batch ID")
        integer(value, 0, 10**9, "Processed round")
    from .probe_plan import verified_tasks
    task_files = verified_tasks(files, parse_json(files["pool/probe-plan.json"]), rows, index["source_fetched_at"])
    expected = assemble({r["id"]: r for r in rows}, files, state["processed_batches"], index["source_fetched_at"],
                        task_files=task_files, legacy=task_files is None)
    require(expected.files == files, "Pool derived files mismatch")
    return expected


def read_pool(root):
    root = Path(root)
    path = root / POOL_INDEX
    if not path.exists():
        require(not (root / "pool").exists() or not any((root / "pool").iterdir()), "Pool exists without an index")
        return None, None
    require(path.stat().st_size <= MAX_INDEX, "Oversized pool index")
    index = validate_pool_index(parse_json(path.read_bytes()))
    files = {}
    for item in (root / "pool").rglob("*"):
        if item.is_file() and item != path:
            require(not item.is_symlink() and item.stat().st_size <= MAX_FILE_BYTES, "Invalid pool file")
            files[item.relative_to(root).as_posix()] = item.read_bytes()
    verify_pool(index, files)
    return index, files
