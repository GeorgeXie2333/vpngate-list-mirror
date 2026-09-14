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
| `pool/task-plan.json` | Version 2 descriptors for absolute execution slots; referenced and hashed by the probe plan |
| `pool/tasks/<worker>-<slot>.json` | At most 35 target nodes, across the Worker partition, with per-target due times |
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
| `hostname`, `ip` | Original source identifier (underscores allowed; see [v1 name rules](protocol.md#normalized-nodes)) and canonical CSV IP |
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

Failure round = `floor(unix_seconds / 21600)`, aligned to UTC 00/06/12/18.
It remains six hours even though regular checks now target four hours. Bucket =
`int(id_without_v1_prefix[:8], 16) % 144`; Worker 0 owns 0–71 and Worker 1 owns
72–143. The two Workers retain their five-minute Cron schedules and do not claim
independent geographical vantage points.

The publisher fills task packs across buckets within each Worker's partition.
Each pack contains at most 35 nodes and 35 distinct target endpoints, reserving
five endpoint slots for current-source controls. Never-attempted and overdue
four-hour checks go first, oldest first. Unknown results become eligible for an
opportunistic retry after one hour, only using capacity left by regular checks.
Four hours is a target, not a guarantee; overflow and platform failures can delay it.

`pool/probe-plan.json` remains schema 1 and retains legacy buckets. Its additive
`task_protocol_version: 2` and `task_manifest` descriptor identify
`pool/task-plan.json`. This schema-2 manifest maps `worker_id:slot` to task file
path/bytes/SHA-256; `slot = floor(scheduled_milliseconds / 300000)` is absolute.
`pool/tasks/<worker_id>-<slot>.json` contains schema 2, worker ID, slot, nominal
`scheduled_at`, and targets with `next_probe_at`. All four reads (Raw pool index,
probe plan, task manifest, selected task) pin to the index's full commit after
discovery and validate sizes/hashes. No whole-pool download or KV scheduling lock
is needed. A missing task produces `no_due_targets`; it cannot select another slot.

Tasks are reserved in Git. Each publication preserves existing assignments through
30 minutes ahead and replans the later three-hour fallback horizon. Deleted nodes
and changed configurations are removed from reservations, and target metadata is
refreshed from the current pool. The past three hours of assignments remain for
delayed Cron delivery. Recent reservations prevent immediate duplicate assignment;
missing results reserve work for only 30 minutes and never count as observations.
There is no exactly-once execution guarantee: delayed/duplicate events and eventual
KV visibility are handled by idempotent result merging. Normal scheduling still
makes at most 576 KV writes per day, one unique object per nonempty invocation.

Only pure TCP configs with explicit ports and public numeric remote addresses equal
to CSV IP are eligible. Up to eight distinct top-level remotes and explicit protocol
overrides are supported. Domains, proxies, includes, connection blocks and uncertain
protocols remain records without guessed endpoints. Source observations older than
three hours pause probing; invocation delay is independently limited to three hours.
Task selection uses scheduled time; failure rounds use actual execution time.

Each invocation processes at most 40 unique endpoints including up to five controls,
with four concurrent sockets, a three-second connection timeout and 45 seconds of
probe budget. No application data is sent. Any successful endpoint establishes TCP
reachability; all endpoints must complete with valid failures to mark unreachable.
Platform restrictions, port 25, resource errors and unclassified exceptions remain
unknown. Every socket is actively closed. Cleanup waits one second for `close()` or
`closed` to fulfill; rejection alone is not confirmation. An unconfirmed slot stays
reserved while other lanes continue. If more work remains, it may wait up to five
additional seconds for positive closure confirmation and then reuse that slot.
Recovery waiting reserves four seconds for the next connection and cleanup within
the same 45-second budget. Unconfirmed slots are never reused, and at most four
sockets can remain outstanding, leaving capacity for KV. Confirmed `opened` success
survives cleanup trouble; an ambiguous failed connection stays unknown.

Result batches use schema 2 for task packs and schema 1 for legacy buckets. V2 replaces
`bucket`/`bucket_sha256` with `task_slot`, `task_path`, `task_sha256` and
`task_manifest_sha256`; other result/guard fields and KV key/TTL are unchanged.
[Probe v2 schemas](../schemas/probe/v2/) document the task plan, task and batch.
Actions accepts both versions and validates the Worker's partition, configuration,
endpoints and times. It reads completed current/previous-round batches without waiting
for probes, and never connects to node IPs. Hashes establish consistency, not an
independent authentication of Worker observations.

A result whose every endpoint is `budget` is pure deferral: it does not overwrite
status, `checked_at`, attempt time, or a failure streak. Partial attempts can still
produce unknown. Optional `tcp_probe.last_attempted_at`/`last_attempt_status` track
the latest processed attempt separately from the retained result. Same-round success
still wins, so a later failed attempt can update attempt age while `checked_at` and
reachable status continue to refer to the earlier success. Historical records without
these fields fall back to `checked_at` for scheduling; old unknown records cannot be
retroactively distinguished from previously recorded deferrals.

`pool.probe_diagnostics` in the Actions log and summary reports:

- `reported_nodes`, `attempted_nodes`, `definitive_nodes`, `unattempted_nodes`:
  result rows, rows with any processed endpoint, reachable/unreachable rows, and
  completely deferred rows. They exclude controls and are batch totals before
  per-node stale/same-round conflict resolution, not unique pool changes.
- `processed_endpoints` includes locally rejected unsupported targets;
  `socket_attempts` excludes unsupported and budget results. Both include controls
  and count unique endpoints within each batch, not across batches.
- `endpoint_error_counts` and `cleanup_error_counts` are recomputed from validated
  endpoint records. `close_timeout`/`close_rejected` describe the initial cleanup
  deadline; later confirmed releases are counted by `recovered_sockets`.
- `unclosed_sockets`, `recovered_sockets`, `budget_exhausted_batches` and
  `socket_stopped_batches` distinguish retained slots, recovered slots, time budget
  exhaustion and all-four-slots-blocked stops. `deferred` includes rollout/capacity
  exclusions and partially or completely unattempted nodes. These are not failures.

Diagnostics travel in the existing batch object and need no additional KV write.
Worker logs expose `schema_version`, `task_slot`, `attempted_endpoints`,
`endpoint_error_counts`, `unclosed_sockets`, `recovered_sockets`, `budget_exhausted`
and `stop_reason`. The public catalog does not contain per-endpoint error logs.

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
  index/plan/task, 256 KiB per legacy probe bucket, 128 KiB decoded config. Invalid/over-capacity current source or an
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
