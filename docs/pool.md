# Rolling pool protocol v1

[中文](pool.zh-CN.md) · [Worker deployment](workers.md) · [Single-response v1 protocol](protocol.md)

The pool accumulates observations from successful official API responses. It
does not claim all worldwide nodes or independent VPN verification. Git is the
authoritative state; KV is an expiring inbox of Worker observations. Original
mirror endpoints retain their existing single-response semantics.

## Files and compatibility

| Path | Purpose |
| --- | --- |
| `pool/latest.json` | `kind: "vpngate-pool"`, `schema_version: 1`, successful source fetch time, generation times, full data commit and file descriptors |
| `pool/servers.json` | Sorted unique nodes, upstream metrics, observations, config references and TCP state |
| `pool/countries.json` | Country codes, original names and counts, including unknown countries |
| `pool/configs/<decoded-sha256>.json` | Exact original Base64 plus decoded configuration SHA-256 and byte size |
| `pool/probe-plan.json` | 144 bucket descriptors and at most five current-source single-endpoint controls |
| `pool/probes/000.json` … `143.json` | Lightweight ID/config hash/IP/endpoints/last-observed/prior-round records; no certificates or Base64 |
| `pool/state.json` | Processed batch UUIDs with rounds; maintenance state committed atomically with the pool |

[JSON Schemas](../schemas/pool/v1/) describe these documents and Worker batches.
New optional fields are compatible; consumers may ignore them. Changing existing
types, units, ID derivation, required paths or meanings requires a new schema
version and coordinated publisher/Worker/consumer migration. Preserve supported
v1 interfaces during transition. Unknown versions fail closed and keep old caches.

All times are UTC RFC 3339 with milliseconds, e.g. `2026-09-12T12:00:00.000Z`.
`source_fetched_at` is this project's successful source retrieval, **not the
upstream directory update time**. `generated_at` is generation of referenced
data; `index_generated_at` is index creation. Successful push and observed CDN
visibility are reported in Actions, not asserted by pre-push index timestamps.

The index includes `source_url`, `server_count` and `workflow_run_url`.
`files` describes both catalogs, the probe plan and state using exact `bytes`
and `sha256`. Each catalog carries the same source time/count. The probe plan
describes each bucket's path/hash/size. Node descriptors identify config files.
Every referenced path is relative to the repository root, pinned to the index's
full 40-character `data_commit`. Do not use the root mirror index for pool files.

## Node fields

| Field | Meaning |
| --- | --- |
| `id` | Existing `v1:` + SHA-256 of UTF-8 `vpngate-node-v1\0<normalized-hostname>\0<canonical-ip>`; port/config changes do not change identity |
| `hostname`, `ip` | Original hostname and canonical CSV IP |
| `country_code`, `country_name` | Uppercase two-letter code / original upstream name, or `null`; unknown country remains included |
| `score`, `ping_ms`, `speed_bps`, `num_vpn_sessions` | Nonnegative safe integers or `null`; upstream values in score units, milliseconds, bits/s, session count |
| `first_seen_at`, `last_seen_at` | Earliest/latest successful source observation retained in this pool; no Git-history backfill |
| `present_in_latest_source` | Whether the latest API response contains this ID, not a live/online flag |
| `openvpn_config_sha256`, `openvpn_config_bytes` | Hash/size of decoded configuration bytes |
| `config` | `{path, sha256, bytes}` of the JSON config file, distinct from decoded content hash/size |
| `probe_targets`, `probe_reason` | Explicit validated endpoints, or empty with reason `udp`, `non_public_ip`, `complex_configuration`, `ambiguous_endpoint`, `ambiguous_protocol` |
| `tcp_probe` | State described below, attributed to `cloudflare_workers` |

`tcp_probe.status` is `reachable`, `unreachable`, `unknown` or `not_applicable`.
It includes nullable `checked_at`, `last_success_at`, `round`, `worker_id`,
`connect_ms`, `connected_endpoint`, `last_failure_round`, plus integer
`consecutive_failures`. `connect_ms` is successful connection establishment
time, separate from upstream `ping_ms`; it is not throughput or ICMP latency.
`connected_endpoint` identifies the successful IP/port. Unchecked and unsupported
records keep null probe times. UDP is `not_applicable`; ambiguous configs remain
`unknown`. Reappearing nodes clear failure counters; a config change resets all
old-config probe state. A reachable observation remains historical: check its age.

Config JSON has `kind: "vpngate-pool-config"`, schema 1 and the three fields
`openvpn_config_base64`, `openvpn_config_sha256`, `openvpn_config_bytes`.
Decode canonical Base64 without normalizing text or line endings, verify the
decoded bytes, and save only if needed. No parser or example executes directives.

## Scheduling and merge rules

Round = `floor(unix_seconds / 21600)`, aligned to UTC 00/06/12/18 hours.
Bucket = `int(id_without_v1_prefix[:8], 16) % 144`. Worker 0 handles buckets
0–71, Worker 1 handles 72–143. Within a six-hour round the slot is
`floor((scheduled_milliseconds % 21600000) / 300000)`; each Worker selects
`worker_id * 72 + slot`. Its bucket's oldest checked nodes go first. Six hours
is a target interval, not a promise: missed invocations and overflow are deferred.
The two Workers do not claim independent geographical vantage points.

Workers fetch a Raw index, then the immutable hash-checked plan and selected
bucket, pausing if source observations are older than three hours. Only pure TCP
configs with explicit ports and public numeric remote addresses equal to CSV IP
are eligible. Multiple top-level remotes and explicit protocol overrides are
supported (up to eight unique endpoints). Domains, proxies, includes, connection
blocks and uncertain/mixed protocols remain records without guessed endpoints.

Each invocation establishes at most 40 distinct endpoints including up to five
current-source controls, with four concurrent sockets, three seconds per connect
and 45 seconds of probe budget. A success on any endpoint marks TCP reachable;
only all completed endpoint failures can mark unreachable. Platform restrictions,
port 25, resource errors and unclassified exceptions are unknown. No application
data is sent. Sockets are actively closed, including timeout paths.

Actions reads completed batches for current/previous rounds, never waits for a
probe to finish and never connects to node IPs. Batches carry unique UUIDs,
worker/bucket/round, full data SHA, plan/bucket hashes, start/end times, endpoint
results, controls, completed/deferred counts and a guard flag. The merger
independently validates and recomputes outcomes/guards. Authenticated Worker
results are an observation source, not independently authenticated by their hashes.

- Ignore results for removed nodes/old configs, older rounds, over 12 hours old,
  or anomalous times (up to five minutes clock tolerance; two-minute batch limit).
- A duplicate round counts at most once; same-round success wins. Round gaps
  restart the failure streak. Source observations after the probe prevent old
  failures from driving removal.
- No matching successful control, or at least ten completed nodes with 80% or
  more failures, guards the batch against failure accumulation/TCP removal.
- Absence alone retains a node. A **new valid failure** may remove it once absent
  at least 24 hours and three consecutive rounds have failed. With no new result,
  an old streak does not trigger cleanup. Unknown/UDP/deferred results add no failures.
- After seven days without source observation, expire the record even if TCP
  reachable. Capacity eviction removes oldest absent records first and is reported
  separately. Current-source records take priority.
- Limits: 5,000 nodes, 64 MiB referenced config JSON, 16 MiB per catalog, 64 KiB
  index/plan, 256 KiB per probe bucket, 128 KiB decoded config. Invalid/over-capacity current source or an
  invalid previous pool stops publication without overwriting the last success.

KV uses unique `results/<round>/<uuid>` keys with a 72-hour TTL, one write per
nonempty invocation. No shared latest key or per-node writes. Eventual consistency
can delay listing or reads; missing results are not failures. Applied batch IDs
live in Git and acknowledge only with a confirmed fast-forward push. Replays are
idempotent. KV read failure pauses TCP updates/cleanup but allows source publishing.

## Public consumers

Discovery: [pool/latest.json](https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/pool/latest.json).
Files: `https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@COMMIT/PATH`.
Fallback: `https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/COMMIT/PATH`.
Replace COMMIT with the pool index's complete SHA and PATH with its validated
descriptor. SHA-256 checks integrity and consistency, not independent source trust.

```bash
curl --fail --silent --show-error --max-time 30 --max-filesize 65536 \
  https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/pool/latest.json
python examples/consume_pool.py --country JP --output selected.ovpn --max-age-hours 24
node examples/consume_pool.mjs JP selected.ovpn
```

```python
from mirror.pool_consumer import load_pool, load_pool_config
repo = "GeorgeXie2333/vpngate-list-mirror"
index, servers = load_pool(repo)  # Verify both catalogs, then atomically switch cache pointer.
japan = [node for node in servers if node["country_code"] == "JP"]
if japan:
    config_bytes = load_pool_config(repo, index, japan[0])  # Lazy file + decoded hash checks.
print(index["source_fetched_at"], len(japan))
```

```javascript
import {loadPool, loadPoolConfig} from "./examples/consume_pool.mjs";
let current = null;
async function refresh() {
  const next = await loadPool({previous: current});
  current = next; // Only after all catalog checks succeed; rejection preserves old data.
  const japan = current.servers.filter(n => n.country_code === "JP");
  if (japan.length) {
    const bytes = await loadPoolConfig(current, japan[0]);
    console.log(current.index.source_fetched_at, japan[0].tcp_probe, bytes.length);
  }
}
await refresh(); // UI should catch errors and show the retained cache's age.
```

Serve both JS modules from an HTTPS origin. Anonymous Fetch and Web Crypto work
with the same Raw/jsDelivr CORS behavior as existing mirror examples; credentials
are omitted and no token/ETag access is needed. Python uses `.cache/vpngate-pool`
with one atomic catalog pointer and content-addressed config cache; use one writer
per cache. Clients choose their own expiry and local config-cache garbage collection.
Suggested warnings: source older than 3 hours, very stale after 24 hours, TCP
observation older than 12 hours. Python `--tcp-reachable` additionally selects
only reachable observations no older than 12 hours. A disappeared pool record
may have expired or hit a capacity limit; do not label it proven offline.
