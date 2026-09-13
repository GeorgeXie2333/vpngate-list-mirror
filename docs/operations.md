# Operations

[中文](operations.zh-CN.md) · [Protocol](protocol.md)

## Minimal GitHub settings

Keep the repository public and set `main` as its default branch. Enable Actions
and allow the pinned official `actions/checkout` and `actions/setup-python`
actions on standard GitHub-hosted Ubuntu runners. Default token permissions may
remain read-only; only the `publish` job in `sync.yml` requests `contents: write`.
Do not enable paid/larger runners or upload snapshot artifacts on each run.

The target branch must allow normal fast-forward pushes by the job's built-in
`GITHUB_TOKEN`. Mandatory PRs, signatures or checks that the token cannot satisfy
will stop automatic publication. Do not enable force-push to work around this.
If organization policy mandates PR-only code branches, move generated data and
the discovery index to a dedicated publication branch and update the documented
index URL as a reviewed architecture change. Publication itself needs no PAT;
the external Worker uses a separate dispatch credential as described below.

No Pages, database, private consumer API, CDN account or purge permission is
required. The scheduler needs a Cloudflare account and a Worker secret; consumers
never use a GitHub token. Standard public-repository runner
usage is free under [GitHub's documented rules](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Refresh and scheduling

Open **Actions → Sync VPN Gate → Run workflow**, select `main`, and run it. With
an existing authenticated maintainer CLI, the equivalent is:

```bash
gh workflow run sync.yml --repo GeorgeXie2333/vpngate-list-mirror --ref main
gh run list --repo GeorgeXie2333/vpngate-list-mirror --workflow sync.yml --limit 5
```

The active scheduler is Cloudflare Worker `vgate-list-update`. Its Cron Trigger
is `29,59 * * * *`: minutes 29 and 59 of every hour in UTC, or 48 planned attempts
per day. Maintain this trigger in Cloudflare **Workers & Pages → vgate-list-update
→ Settings → Triggers → Cron Triggers**. The same minute values apply in UTC+8.
[Cloudflare documents](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
UTC scheduling and up to 15 minutes for trigger changes to propagate.

The deployed Worker's `scheduled()` handler sends a POST to
`https://api.github.com/repos/GeorgeXie2333/vpngate-list-mirror/actions/workflows/sync.yml/dispatches`
with JSON body `{"ref":"main"}`. `sync.yml` intentionally has only
`workflow_dispatch`, supporting both this API and the manual button. Keep it on
the default branch and keep the workflow enabled; do not add a second GitHub
`schedule`. Publication from other refs is skipped. The GitHub native schedule's
60-day inactivity rule is not the scheduling mechanism used here.

Store the dispatch credential as **Secret** `GH_ACTIONS_TOKEN` in the Worker,
not in this repository or a consumer example. For a fine-grained PAT, select
only this repository and grant **Actions: Read and write**; the dispatch token
does not need Contents write. See the [GitHub dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).
Rotate it before expiration and update the Worker secret. The Actions publication
job continues to use its own short-lived `GITHUB_TOKEN`.

Keep the Worker's bounded request timeout and `redirect: "manual"`; accept only
HTTP 200 or 204, rejecting redirects and other statuses. `redirect: "error"`
caused a runtime exception in this deployment. Do not follow redirects with the
authorization header ([Cloudflare request behavior](https://developers.cloudflare.com/workers/runtime-apis/request/)).
Check the Worker's `scheduled` event and `workflow_dispatched` log, then the
corresponding Actions run and its publication summary. A GET to the deployed
`/__scheduled` URL does not invoke this Worker's scheduled handler; its HTTP
handler returns 404. Do not expose an unauthenticated HTTP dispatch endpoint.

`concurrency: vpngate-sync` and `cancel-in-progress: false` keep Worker and manual
runs from cancelling an active sync; GitHub may replace older pending runs.
Cron time, dispatch acceptance, runner start, successful publication and CDN
visibility are separate events. Queuing and service failures remain possible;
this does not guarantee publication exactly every half hour. After an ambiguous
dispatch timeout, check recent runs before retrying to avoid duplicate requests.

The optional **Optional live source check** workflow (or
`python -m mirror check-live`) fetches and validates real HTTPS data without
publishing. Ordinary PR and push checks use only offline fixtures.

## Publication transaction

The sync command runs the full Python and JavaScript offline suites alongside
one source fetch. Both suites must pass and the fetch must succeed before data
generation or publication; test subprocesses receive no publication token.
It generates deterministic data in memory and clones only the publication branch
tip into its own temporary checkout. A code/schema/data change since the tested
source revision stops publication; documentation-only movement can be retained.
Only the three mirror data files, `latest.json` and generated `pool/` files are staged.

For changed bytes, the local graph is `remote H → data D → index I`. Read D's
actual full SHA, embed it in I's index, validate all files, then push I with a
single normal fast-forward branch update. This makes both commits available
together and avoids a commit self-reference. The workflow checkout's
`GITHUB_SHA` is not substituted for D. On identical bytes, keep D and its data
generation time and create only a small index commit for the new successful
fetch. No timestamp is written into the original mirror data files. Pool
catalogs contain observation times and change on successful retrieval; unchanged
configuration blobs are reused. Each dataset's index can reference a different
data commit, but every file within that dataset is pinned to its own index.

A rejected push causes a remote read, not force-push. If it is only a competing
documentation commit, rebuild both D and I on the new tip (at most three push
attempts). Do not rebase D while retaining I's stale SHA reference. A concurrent
code/data edit causes an explicit failure. A newer published fetch supersedes
an older result. An uncertain push acknowledgment is resolved by reading the
remote indexes/head before retrying. Both indexes must match when confirming a
combined pool publication. Processed Worker batch IDs are committed with the
pool state; failed pushes never acknowledge them. Resets/checkouts occur only inside the
publisher's disposable directory, never in a maintainer's checkout.

Authentication is added to Git's process environment after validation, not to
URLs, command arguments or a persistent credentials file. The token is never
sent to the source, CDN or consumers. Checkout uses `persist-credentials: false`.
Sync does not listen to `push`; ordinary token-generated pushes also do not
start push workflows under [GitHub's trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).
PR checks explicitly have read-only repository access.

The published index remains the **latest success**, not a mutable failure log.
Fetch/validation failures leave it and all prior data unchanged and return a
failed Actions run with a summary. Before the very first success these generated
files do not exist; no empty or fabricated success is published.

## Visibility and failure diagnosis

Read these separately in the Actions summary:

| Observation | Meaning |
| --- | --- |
| `attempt_started_at` | Runner started this attempt, not the scheduled cron time |
| `fetched_at` | Source response finished and passed validation |
| `data_changed` / file sizes / counts | Differences and scope of this response |
| `push_confirmed_at` | GitHub accepted the publication ref update, or its success was subsequently confirmed |
| `durations_seconds` | Elapsed time for checks/fetch, validation, publication, visibility probes and the command total; excludes runner queuing/setup |
| `visibility_probes[path].cdn.verified_at` | This runner downloaded and verified this exact SHA file through CDN |
| `visibility_probes[path].raw.verified_at` | Same check through Raw fallback |

New SHA URLs are only probed after push, avoiding intentional pre-publication
404 requests. All six CDN/Raw downloads run concurrently; each retains its size
limit, timeout and complete SHA-256 check, and failures are reported separately.
A failed CDN probe does not undo valid Git data; the summary
reports a warning and the Raw result. These are observations from one network
location, not the time every CDN edge became visible. A pushed snapshot can
temporarily be unavailable to a particular client, which retains its old cache.

| Symptom | Action |
| --- | --- |
| No Worker `scheduled` event | Check the deployed Worker and its Cron Trigger; allow trigger changes to propagate |
| Worker dispatch fails | Check `GH_ACTIONS_TOKEN`, expiration, repository scope, Actions write permission, API status and `redirect: "manual"` |
| Dispatch accepted but no published update | Inspect the Actions run, queue, workflow enabled state and publication summary; acceptance is not completion |
| Source timeout, 429, 5xx | Read bounded retry result; keep old data and retry manually later |
| Empty, truncated or structurally invalid CSV/config | Inspect the error and source format; add offline regression fixtures before changing validation |
| Push denied with unchanged remote | Check job permissions, repository policy and branch rules; do not force-push |
| Concurrent code/data change | Run the workflow again from current `main` after reviewing the competing change |
| Index looks old | Compare `fetched_at`, recent Actions runs and Raw caching; do not assume a cache-busting query guarantees freshness |
| New CDN SHA URL fails | Try same-SHA Raw; retain old cache if neither verifies |
| Hash or multi-file mismatch | Reject the candidate, retain old cache, and inspect the index's run and pinned files |
| Unsupported schema | Keep old verified data, display its age and upgrade the consumer |
| Requested country has no nodes | Do not claim the nodes are offline; choose a currently listed country or wait for a later snapshot |

For CSV validation failures, `validation_error` in the log and task summary gives
the one-based `csv_record` and the record's ending physical line, `csv_line_end`.
Name errors also identify `field`: `HostName` for the CSV column, or
`OpenVPN_ConfigData_Base64.remote.host` for a configuration remote hostname,
including remotes inside `<connection>` blocks. `value_preview` is an ASCII
JSON-escaped preview of the first 96 characters, with the original character
count, truncation flag and UTF-8 SHA-256. `rejected_source` gives the exact failed
response's byte count and SHA-256; it identifies the response but does not retain
a copy. Full CSV, Base64 configurations and certificates are not logged. Failed
validation still prevents publication and preserves the previous good snapshot.

From an HTTPS browser origin, use anonymous `fetch` with `credentials: "omit"`,
read bytes and verify with `crypto.subtle.digest`. Confirm the response is
readable, not opaque, and that all requested data and config hashes match. Do
not use `mode: "no-cors"`, credentials, an API token or manual `If-None-Match`.
`Access-Control-Allow-Origin: *` and currently observed cache headers should be
checked again if browser access changes; they are not controlled by this repo.

## Node pool operation

See [manual Worker/KV deployment](workers.md) and [pool protocol](pool.md).
Repository variables `POOL_ENABLED=false` and `TCP_PRUNE_ENABLED=false` pause
pool updates and TCP-based removal respectively; absent values default to true.
Disabling the pool retains its previous files and lets its visible timestamp
age. Disabling TCP pruning still applies observations/probe statuses, seven-day
record expiry and capacity eviction. No probe reader configured means no new
TCP result updates or TCP removals. A failed reader is a soft error: the original
mirror and source observations still publish.

The summary's `pool` object reports `reader.status/errors/pending`, added/count,
configuration changes/reused bytes, processed/guarded batches, deferred nodes,
oldest probe age, never-probed count and removals split into `tcp_removed`,
`expired`, `capacity_evicted`. KV absence/lag is never counted as node failure.
The pool is seeded only from the existing latest snapshot, not the whole Git history.

Pool publication additionally verifies its two catalogs, probe plan and one
configuration via CDN and Raw at the pool commit. `pool_visibility` reports
these HTTPS downloads; they are not node TCP probes.

## History and dependency maintenance

The 2026-09-11 implementation check returned 100 nodes: 1,347,159 CSV bytes,
1,384,056 JSON bytes and 1,297 country bytes, about 2.73 MB total. If all 48 daily
runs succeed with changed data, that is 17,520 snapshots / 35,040 commits and roughly
48 GB of logical uncompressed file versions per 365-day year. This is **not** an estimate of the actual
Git pack: cross-file compression and deltas depend on real content and order.

The pool adds up to 64 MiB of currently referenced config JSON and catalogs for
at most 5,000 nodes. At 48 publications/day, a 1 MiB changed catalog alone has
about 17.1 GiB/year of logical versions before Git compression. Stable config
content shares a Git blob even as observations change. Newly observed unique
configurations still grow history: measure actual pack growth, not just current
working-tree size. Unreferenced configs are removed only from the working tree.

Record repository size after 7 and 30 days and review monthly. The GitHub repo
API's `size` is an approximate KiB value; `git count-objects -vH` is meaningful
for local history size only after a complete history fetch, not a shallow clone.
Suggested maintainer thresholds are a review at 500 MiB and a migration plan
near 1 GiB. [GitHub recommends](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)
repositories ideally below 1 GB and strongly below 5 GB.

Only the latest files live in the working tree. Deleting them or old working
directories does **not** remove historical Git objects. Do not automate history
rewrites, force-push, or orphan-branch resets. If growth requires a successor
public repository under the same account, announce a new discovery URL and
keep the old repository for pinned history links. A fixed repository, unlimited
immutable history and bounded storage cannot all be guaranteed indefinitely.
jsDelivr may retain cached files even after repository deletions, so deletion
is not a CDN revocation mechanism.

Review Dependabot's weekly Actions updates and verify release-to-SHA provenance.
Keep full SHA pins with version comments; do not replace them with floating
major tags. Record the actual runner and Python patch versions from each run.
Protocol upgrades follow [the protocol document](protocol.md#compatibility-and-upgrades).
