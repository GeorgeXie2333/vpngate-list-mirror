# VPN Gate directory mirror

[中文说明](README.zh-CN.md) · [Data protocol](docs/protocol.md) · [Operations](docs/operations.md) · [Initial verification](docs/verification.md)

A public HTTPS mirror of the server directory returned by the
[official VPN Gate CSV API](https://www.vpngate.net/api/iphone/).
Anyone can download the directory and its complete public OpenVPN configurations:
no login, registration, API key, or consumer-side GitHub token is required.

Cloudflare Worker `vgate-list-update` requests a refresh at minutes **29 and 59
of every hour (UTC)** (`29,59 * * * *`, 48 attempts per day), dispatching GitHub
Actions to fetch, validate and commit a complete response. GitHub Raw serves a small version index;
jsDelivr distributes files pinned to one full data commit SHA.

This mirrors the API response, not every VPN Gate server worldwide. Scores,
Ping, speed, country information, and session counts come from upstream. This
project does not measure node performance or verify that a node is online.
It does not connect to a VPN, execute configuration directives, create a TUN
interface, or change system networking. Directory updates do not guarantee that
a connection will succeed.

## Public downloads

The default download links use `@latest` and need no credentials:

```text
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json
```

Choose another jsDelivr endpoint if it works better on your network. All links
in this table use `@latest`:

| Endpoint | Servers JSON | Original CSV | Countries JSON |
| --- | --- | --- | --- |
| jsDelivr default (`cdn.jsdelivr.net`) | [JSON](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [Countries](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| jsDelivr Fastly (`fastly.jsdelivr.net`) | [JSON](https://fastly.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://fastly.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [Countries](https://fastly.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| jsDelivr Gcore (`gcore.jsdelivr.net`) | [JSON](https://gcore.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://gcore.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [Countries](https://gcore.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| `testingcf.jsdelivr.net` | [JSON](https://testingcf.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://testingcf.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [Countries](https://testingcf.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |
| `quantil.jsdelivr.net` | [JSON](https://quantil.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json) | [CSV](https://quantil.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/vpngate.csv) | [Countries](https://quantil.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/countries.json) |

These are convenient download links and may serve cached data. `@latest` resolves
to the latest semver release, falling back to the default branch when there are
no tagged releases ([jsDelivr resolution rules](https://github.com/jsdelivr/jsdelivr#github)).
It does not guarantee freshness on this schedule or a consistent snapshot across files
or endpoints. Use the index workflow below when those properties matter.

| File | Contents |
| --- | --- |
| `data/vpngate.csv` | Original UTF-8 CSV response bytes, including markers, all columns and full Base64 configurations |
| `data/servers.json` | Normalized unique nodes, upstream metrics, country fields and complete Base64 configurations |
| `data/countries.json` | Country/region groups, original names and unique node counts |
| `latest.json` | Schema version, successful fetch time, generation times, data commit, SHA-256 hashes, byte sizes and counts |

The [fixed branch JSON link](https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@main/data/servers.json)
is convenient for manual inspection but can lag behind the index. It is not the
version-discovery endpoint.

## Refresh and consistency

Read the [latest successful snapshot index](https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/latest.json)
and replace `COMMIT` with its `data_commit`, using the **full 40-character SHA**:

```text
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@COMMIT/data/servers.json
https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/COMMIT/data/servers.json
```

The second URL is the same-commit GitHub Raw fallback. Replace the file name for
CSV or countries; the CDN hostname may also be replaced with one from the table.

1. Fetch `latest.json` once and validate the supported `schema_version`.
2. Keep its `data_commit` fixed while downloading all required files.
3. Check the exact response byte lengths and SHA-256 hashes before parsing or
   using the data. Do not hash reserialized JSON.
4. On CDN failure, try GitHub Raw at the **same commit**.
5. Validate counts and snapshot references, then replace the local snapshot as a
   whole. Retain the last verified cache if any required file fails.

`fetched_at` is this mirror's most recent successful retrieval, not an upstream
directory update time. `source_updated_at` is `null` when upstream does not
provide a reliable value. An unchanged successful response renews `fetched_at`
in the small index while retaining the same data commit and large-file blobs.

The [jsDelivr documentation](https://github.com/jsdelivr/jsdelivr#caching) specifies
12-hour branch caching, 7-day version-alias caching (including `latest`), and
long-lived immutable commit content. `@main`, `@latest`, timestamps in query
parameters and purge requests are not the consistency mechanism. Purge is not
required or used.

GitHub Raw is also cached. An anonymous header probe on 2026-09-11 observed
`Cache-Control: max-age=300`; this is not a contractual refresh deadline.
Browser `cache: "no-store"` controls browser caching, not every upstream cache.
The schedule is managed by [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/),
not a GitHub Actions `schedule`. Worker dispatch, runner start, successful
publication and CDN visibility are separate events. An accepted dispatch does
not mean publication has finished; there is no strict publication deadline or
worldwide CDN visibility guarantee.

Consumers can poll the small index every 10–15 minutes with jitter. Suggested
age indicators are 3 hours for stale and 24 hours for very stale; consumers
choose their own thresholds. Reject older indexes when a newer verified index
is already cached. On an unsupported schema, retain the previous data and
report the incompatibility. A node absent from the next snapshot is removed
from the current directory; its disappearance does not prove it is offline.

SHA-256 provides integrity and cross-file consistency. It is **not independent
source authentication**, because the index and files are distributed through
the same project's publication chain.

## Examples

The repository's Python examples use Python 3.13+ and its standard library. The
JavaScript example needs Node.js 22+ or a browser with Fetch and Web Crypto on
HTTPS. All examples only save/decode configurations; none starts a VPN.

Quick download with the default `@latest` URL (subject to the caching above):

```bash
curl --fail --silent --show-error --location --max-time 30 \
  --max-filesize 16777216 --proto '=https' --proto-redir '=https' \
  https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@latest/data/servers.json \
  --output servers.download.json
```

For a verified snapshot, the following examples use the Raw index and full
commit SHA. From a public checkout, curl fetches the index and Python verifies
the snapshot, selects Japan and writes the unchanged configuration:

```bash
curl --fail --silent --show-error --location --max-time 30 \
  --max-filesize 65536 --proto '=https' --proto-redir '=https' \
  https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/latest.json \
  --output latest.download.json

python3 examples/consume.py --index-file latest.download.json \
  --country JP --output selected.ovpn

# Or run the combined curl/Python script:
bash examples/consume.sh JP selected.ovpn
```

On Windows use `curl.exe` and an installed Python executable. No `jq`, pip
packages, login or API token is needed. Python uses a version directory and one
atomic `current.json` pointer in `.cache/vpngate`. Use one writer per cache
directory. Optional `--max-age-hours 24` rejects an excessively old snapshot
before installing it. An unsuccessful refresh exits nonzero and retains the
last successful cache; a failed selection does not overwrite the output file.

Python API, from the repository root:

```python
from pathlib import Path
from mirror.consumer import load_snapshot
from mirror.snapshot import parse_json
from mirror.validate import decode_config

index, files = load_snapshot("GeorgeXie2333/vpngate-list-mirror")
servers = parse_json(files["data/servers.json"])["servers"]
japan = [server for server in servers if server["country_code"] == "JP"]
if japan:
    Path("selected.ovpn").write_bytes(decode_config(japan[0]["openvpn_config_base64"]))
print(index["fetched_at"], index["data_commit"])
```

JavaScript CLI:

```bash
node examples/consume.mjs JP selected.ovpn
```

JavaScript in an HTTPS page that serves a copy of `examples/consume.mjs`:

```javascript
import { loadSnapshot, decodeConfig } from "./examples/consume.mjs";

let current = null;
async function refresh() {
  const next = await loadSnapshot({ previous: current });
  current = next; // one complete in-memory cache switch, only after verification
  const japan = current.servers.filter(server => server.country_code === "JP");
  if (japan.length) {
    const config = new Blob([decodeConfig(japan[0])], {
      type: "application/octet-stream"
    });
    console.log(current.index.fetched_at, japan.length, config.size);
  }
}
await refresh(); // handle rejection in your UI; current remains intact
```

The default CDN and GitHub Raw support anonymous cross-origin GET in the observed
responses (`Access-Control-Allow-Origin: *`). An anonymous probe of
`countries.json` on 2026-09-11 also returned HTTP 200 and that CORS header from
all five CDN endpoints above; availability can vary by network and time.
The browser example uses `credentials: "omit"`
and no authorization or custom conditional headers. Raw may return JSON as
`text/plain`; parsing after byte verification works regardless. A non-exposed
ETag is not required. The JavaScript module verifies all three file hashes,
node IDs, configuration bytes and country totals before returning a new
snapshot. Python additionally re-derives normalized values from the raw CSV.

## Development and operation

```bash
python -m unittest discover -s tests -v
node --test tests/test_consumer.mjs
python -m mirror build --source-file tests/fixtures/normal.csv --output build/offline
python -m mirror check-live  # optional HTTPS integration check; no publication
python -m mirror verify     # verify a published checkout including latest.json
```

Default tests are offline. They cover quoting, empty values, duplicate and
conflicting nodes, malformed Base64, truncation, empty directories, timeouts,
size limits, old indexes, hash mismatches, mixed snapshots, failed cache
replacement, publication races and an uncertain push acknowledgment.

To refresh, open [Sync VPN Gate](https://github.com/GeorgeXie2333/vpngate-list-mirror/actions/workflows/sync.yml)
and select **Run workflow** on `main`. Only its publication job has
`contents: write`; PR checks are read-only. Publication uses the built-in
`GITHUB_TOKEN`. The external scheduler requires a maintainer's Cloudflare account
and a GitHub dispatch credential stored as the Worker secret `GH_ACTIONS_TOKEN`;
consumers need neither. Official Actions are pinned to complete SHAs and updated
through Dependabot PRs. No self-hosted server, database or Pages site is needed.
See the [scheduler setup](docs/operations.md#refresh-and-scheduling) for permissions,
token rotation and trigger diagnostics.

See [operations](docs/operations.md) for branch settings, troubleshooting,
publication timestamps and history growth, and [CONTRIBUTING](CONTRIBUTING.md)
for changes. The code is MIT-licensed; upstream directory data and configurations
retain their own rights and notices. See [NOTICE](NOTICE).
