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
index URL as a reviewed architecture change. No PAT or new GitHub App is needed
for the default single-repository design.

No Pages, database, private API, secrets, CDN account or purge permission is
required. Consumers never use a GitHub token. Standard public-repository runner
usage is free under [GitHub's documented rules](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## Refresh and scheduling

Open **Actions → Sync VPN Gate → Run workflow**, select `main`, and run it. With
an existing authenticated maintainer CLI, the equivalent is:

```bash
gh workflow run sync.yml --repo GeorgeXie2333/vpngate-list-mirror --ref main
gh run list --repo GeorgeXie2333/vpngate-list-mirror --workflow sync.yml --limit 5
```

The cron expression is `17 */4 * * *`: every four hours at 00:17, 04:17, 08:17,
12:17, 16:17 and 20:17 UTC (six runs per day). The workflow file must exist on
the default branch, and scheduled workflows only run there. Manual publication from
other refs is skipped. `concurrency: vpngate-sync` and
`cancel-in-progress: false` prevent active scheduled/manual runs from replacing
each other. GitHub may replace older pending runs, delay scheduled work, or drop
it under high load. Public repository schedules are automatically disabled
after 60 days without repository activity. Re-enable them from Actions if this
happens; refreshing the data manually does not justify inventing a source time.
See [GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

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
Only the three allowed data files and `latest.json` are staged.

For changed bytes, the local graph is `remote H → data D → index I`. Read D's
actual full SHA, embed it in I's index, validate all files, then push I with a
single normal fast-forward branch update. This makes both commits available
together and avoids a commit self-reference. The workflow checkout's
`GITHUB_SHA` is not substituted for D. On identical bytes, keep D and its data
generation time and create only a small index commit for the new successful
fetch. No timestamp is written into the large data files.

A rejected push causes a remote read, not force-push. If it is only a competing
documentation commit, rebuild both D and I on the new tip (at most three push
attempts). Do not rebase D while retaining I's stale SHA reference. A concurrent
code/data edit causes an explicit failure. A newer published fetch supersedes
an older result. An uncertain push acknowledgment is resolved by reading the
remote index/head before retrying. Resets/checkouts occur only inside the
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
| Source timeout, 429, 5xx | Read bounded retry result; keep old data and retry manually later |
| Empty, truncated or structurally invalid CSV/config | Inspect the error and source format; add offline regression fixtures before changing validation |
| Push denied with unchanged remote | Check job permissions, repository policy and branch rules; do not force-push |
| Concurrent code/data change | Run the workflow again from current `main` after reviewing the competing change |
| Index looks old | Compare `fetched_at`, recent Actions runs and Raw caching; do not assume a cache-busting query guarantees freshness |
| New CDN SHA URL fails | Try same-SHA Raw; retain old cache if neither verifies |
| Hash or multi-file mismatch | Reject the candidate, retain old cache, and inspect the index's run and pinned files |
| Unsupported schema | Keep old verified data, display its age and upgrade the consumer |
| Requested country has no nodes | Do not claim the nodes are offline; choose a currently listed country or wait for a later snapshot |

From an HTTPS browser origin, use anonymous `fetch` with `credentials: "omit"`,
read bytes and verify with `crypto.subtle.digest`. Confirm the response is
readable, not opaque, and that all requested data and config hashes match. Do
not use `mode: "no-cors"`, credentials, an API token or manual `If-None-Match`.
`Access-Control-Allow-Origin: *` and currently observed cache headers should be
checked again if browser access changes; they are not controlled by this repo.

## History and dependency maintenance

The 2026-09-11 implementation check returned 100 nodes: 1,347,159 CSV bytes,
1,384,056 JSON bytes and 1,297 country bytes, about 2.73 MB total. If all six daily
runs succeed with changed data, that is 2,190 snapshots / 4,380 commits and roughly
6 GB of logical uncompressed file versions per 365-day year. This is **not** an estimate of the actual
Git pack: cross-file compression and deltas depend on real content and order.

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
