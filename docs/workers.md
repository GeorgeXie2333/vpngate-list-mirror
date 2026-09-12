# Deploy the TCP probe Workers yourself

[中文](workers.zh-CN.md) · [Pool protocol](pool.md)

This repository provides the source and pinned deployment tools. Deployment,
KV creation and the live acceptance checks below are maintainer operations.
There is no automatic Cloudflare deployment or paid-plan upgrade in Actions.
Keep the existing `vgate-list-update` Worker and `29,59 * * * *` cron unchanged.

## Settings

| Setting | Where | Value |
| --- | --- | --- |
| `RESULTS` | Both probe Workers | Same new KV namespace binding |
| `WORKER_ID` | Worker variables | `0` or `1`, as text or a JSON number; other values are rejected |
| `REPOSITORY` | Both Worker variables | `GeorgeXie2333/vpngate-list-mirror` |
| `MAX_TARGETS_PER_RUN` | Both Worker variables | `2` for initial small-batch acceptance, then `40` (allowed 1–40) |
| `PROBE_READ_TOKEN` | Worker 0 secret and GitHub Actions repository secret | Same random secret of at least 32 characters, separate from the dispatcher credential |
| `PROBE_RESULTS_URL` | GitHub Actions repository variable | Worker 0 HTTPS origin, e.g. `https://vgate-tcp-probe-0.YOUR-SUBDOMAIN.workers.dev`, no path/query |
| `POOL_ENABLED` | GitHub repository variable | Unset/`true` enables accumulation; `false` freezes pool while mirror continues |
| `TCP_PRUNE_ENABLED` | GitHub repository variable | Start with `false`; set `true` after live acceptance. Unset defaults to true |

Worker 1 needs no secret. Neither probe Worker holds GitHub write/dispatch
credentials. The public mirror/pool do not use `PROBE_READ_TOKEN`; it protects
only the operational batch reader. A missing reader leaves accumulation enabled
and TCP cleanup paused. Source retrieval failure still retains both old indexes.

## Dashboard deployment (no Wrangler)

Download [worker-dashboard.js](../workers/worker-dashboard.js) using GitHub's **Raw** button,
then paste the complete file into both probe Workers and deploy each production version.
Keep their individual variables, shared `RESULTS` binding and Cron schedules from the settings above.
Start with `MAX_TARGETS_PER_RUN=2` and `TCP_PRUNE_ENABLED=false`.

This file is generated from `workers/src` with `node workers/build-dashboard.mjs`, using
only Node.js built-ins. The command also refreshes the identical local `build/worker-dashboard.js`
copy. CI checks the committed file against its sources; edit the sources and regenerate it.

Each scheduled invocation logs `started`, followed by `stored`, `no_due_targets` or `failed`.
If a socket's closure cannot be confirmed, the batch stops starting new connections,
marks the affected endpoint `unknown`, defers remaining work, and still attempts one KV write.
The stored summary includes `stop_reason: socket_close_unconfirmed` and `unclosed_sockets`.
At most four sockets remain outstanding, leaving capacity for the KV request.

Cron delivery may be up to three hours late, provided source observations are still at most
three hours old. The original scheduled time chooses the bucket; the actual probe execution
chooses the result round, so delayed work cannot backfill an old failure round.

## Install and deploy with Wrangler (optional)

1. Allow the new repository code to publish an initial `pool/latest.json` using
   **Actions → Sync VPN Gate → Run workflow → main**. Check its source time is
   under three hours old. Set repository variable `TCP_PRUNE_ENABLED=false`.
2. Check the account has two free Cron slots and sufficient KV quotas. With the
   existing dispatcher this project uses three Cron Triggers. Create one KV
   namespace, such as `vpngate-probe-results`, in the Cloudflare dashboard and
   record its namespace ID.
3. Install the lockfile's deployment dependency and create ignored local configs:

   ```bash
   cd workers
   npm ci
   cp wrangler.probe-0.jsonc wrangler.local.probe-0.json
   cp wrangler.probe-1.jsonc wrangler.local.probe-1.json
   ```

   In PowerShell use `Copy-Item` instead of `cp` if needed, and `npm.cmd` /
   `npx.cmd` if script execution policy blocks the wrappers. Replace
   `REPLACE_WITH_SHARED_KV_NAMESPACE_ID` in **both local files** with the same ID.
   Change Worker 0's `MAX_TARGETS_PER_RUN` to `"2"` for initial acceptance.
   Keep repository templates free of account-specific secrets and local settings.

4. Authenticate Wrangler to your own Cloudflare account and deploy only Worker 0
   initially. The lockfile pins Wrangler 4.131.1; `--no-install` prevents fetching
   an unexpected release.

   ```bash
   npx --no-install wrangler login
   npx --no-install wrangler deploy --dry-run --config wrangler.local.probe-0.json
   npx --no-install wrangler deploy --config wrangler.local.probe-0.json
   npx --no-install wrangler secret put PROBE_READ_TOKEN --config wrangler.local.probe-0.json
   ```

   Enter the generated secret through Wrangler's interactive prompt. Add the
   same value to GitHub **Settings → Secrets and variables → Actions → Secrets**,
   and the deployed Worker 0 origin to **Variables → PROBE_RESULTS_URL**. Do not
   commit secrets or place them in a consumer URL. The KV ID itself is not a secret.

5. Complete the small-batch checks below. Then set Worker 0's rollout limit to
   `"40"`, deploy it again, and deploy Worker 1 from its local config:

   ```bash
   npx --no-install wrangler deploy --config wrangler.local.probe-0.json
   npx --no-install wrangler deploy --dry-run --config wrangler.local.probe-1.json
   npx --no-install wrangler deploy --config wrangler.local.probe-1.json
   ```

   Confirm Cron `2-57/5 * * * *` on Worker 0 and `4-59/5 * * * *` on Worker 1.
   Both use UTC. Allow up to 15 minutes for [Cron configuration propagation](https://developers.cloudflare.com/workers/configuration/cron-triggers/).
   Review normal-batch CPU usage and a complete six-hour round, then set
   `TCP_PRUNE_ENABLED=true`. A six-hour target interval can slip under load or
   missed schedules; it is not a deadline guarantee.

## Required live acceptance

- Inspect actual **scheduled** events in Cloudflare logs, not an HTTP GET to
  `/__scheduled`. Production HTTP handlers never trigger probes. Wait for a
  nonempty bucket; empty/due-filtered buckets correctly log `no_due_targets`.
- Confirm `socket.opened` success, immediate close, and classification of
  platform errors as unknown. A run has at most two target nodes during initial
  rollout (plus up to five controls); no application data or OpenVPN is sent.
- Check Worker metrics for actual CPU time, wall time, resource exceptions and
  subrequests. Network wait is not CPU time. Small-batch success does not prove
  40-endpoint batches fit the free CPU budget: re-check after rollout.
- Confirm a single immutable `results/<round>/<uuid>` KV object with 72-hour
  expiration; inspect results/control/guard/deferred fields. Empty buckets may
  make no write. Listing/get may lag across locations.
- After the next existing half-hour Actions run, confirm `pool.reader.status`
  is `read`, `batches_applied` grows, batch UUID appears in committed
  `pool/state.json`, and matching node TCP observations update. No extra Actions
  runs are needed. A manual run can shorten this initial validation wait.
- Download the published pool/config with the new consumers. Check Raw/CDN
  hashes, timestamps and summary removal reasons. Leave pruning disabled if
  the platform is failing or CPU limits are exceeded; shrink the rollout limit
  instead of enabling a paid plan automatically.

Offline tests use fake sockets and KV and cannot certify the deployed platform's
CPU usage or real TCP results. Record deployment version, resource measurements
and run URL when completing this checklist.

## Troubleshooting empty KV

Check scheduled probing, KV writes and Actions reads separately:

| Observation | Check and meaning |
| --- | --- |
| Only `fetch` / `GET /v1/batches` logs | These are Actions reads; they do not prove a scheduled probe ran |
| Cron exists but no `scheduled` logs | Open **Settings → Trigger Events → View events** for the deployed production Worker |
| Cron execution history is also empty | Verify the production deployment has the `scheduled` handler, the saved Cron remains listed, and the account has Cron capacity; this alone does not identify a KV fault |
| `status: no_due_targets` | The bucket is empty or already completed this round; no KV write is expected. Check subsequent buckets |
| `status: stored` but the KV view is empty | Compare both Workers' `RESULTS` namespace IDs with the namespace being viewed |
| Actions `pool.reader.errors` includes `http_404` | The listing API must use Worker 0's origin; Worker 1 always returns 404. A single batch 404 can also mean delayed visibility |
| Actions reports `http_401` | Check that Worker 0 and Actions have the same `PROBE_READ_TOKEN`; do not paste secrets into logs |
| Actions `reader.status: read` and `batches_read: 0` | The read succeeded with no new visible batches; this is not a node failure |

For dashboard deployments, save the JS, variables, KV binding and Cron separately
on each Worker. Use **Settings → Triggers → Cron Triggers**, with `2-57/5 * * * *`
for Worker 0 and `4-59/5 * * * *` for Worker 1. Wrangler is not required.
The [official documentation](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
allows up to 15 minutes for Cron changes to propagate and up to 30 minutes for
the first history entries after creating or renaming a Worker. If history stays
empty beyond those intervals, check the production deployment and saved triggers,
then investigate platform issues.

## Read API and limits

Worker 0 accepts only authenticated GET requests with
`Authorization: Bearer <PROBE_READ_TOKEN>`:

- `/v1/batches?round=<integer>&cursor=<optional>` lists current/previous round
  keys, `list_complete` and `cursor` (up to 200 keys/page).
- `/v1/batch/results/<round>/<uuid>` reads a listed batch; a temporary 404 can
  mean it is not visible yet. Do not confirm consumption on failed reads.

Worker 1's HTTP handler returns 404. The reader cannot accept targets, write
results or start probing. Unauthorized requests return 401; writes return 405.
Responses are `Cache-Control: no-store`. To rotate the read secret, update both
Worker 0 and GitHub; a brief mismatch pauses TCP processing while collection continues.

[TCP sockets](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/)
provide `opened` and `close()` and prohibit Cloudflare destinations, private/
localhost targets and port 25. The code additionally rejects nonpublic numeric
addresses and maps unclear platform errors to unknown. Multiple Workers do not
represent independent geographic measurements.

As checked 2026-09-12, [Workers Free limits](https://developers.cloudflare.com/workers/platform/limits/)
include 10 ms CPU per invocation, 50 subrequests, six simultaneous outgoing
connections and five Cron Triggers/account. This implementation uses three
small GitHub Raw reads, up to 40 socket calls, at most four concurrent sockets
and one KV write per nonempty invocation. Observe actual platform accounting.
Both probe Workers together schedule at most 576 writes/day, below
[KV Free's 1,000 writes/day](https://developers.cloudflare.com/kv/platform/limits/)
provided other account projects leave enough quota. Reads/lists and storage
also share account quotas; use the dashboard to check them.

[KV is eventually consistent](https://developers.cloudflare.com/kv/concepts/how-kv-works/),
including negative cache entries; remote visibility can take 60 seconds or more.
There is no shared latest key or assumption of atomic KV updates. Pause the two
probe crons or unset `PROBE_RESULTS_URL` to stop processing without affecting the
existing mirror dispatcher. Do not purge Git history when retiring a config.
