# Data protocol v1

[中文](protocol.zh-CN.md) · [README](../README.md)

## Files and version discovery

The only mutable discovery document is
`https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/main/latest.json`.
Fetch it once per refresh. All data paths in its manifest resolve at its one
`data_commit`, using either:

```text
https://cdn.jsdelivr.net/gh/GeorgeXie2333/vpngate-list-mirror@DATA_COMMIT/PATH
https://raw.githubusercontent.com/GeorgeXie2333/vpngate-list-mirror/DATA_COMMIT/PATH
```

`data_commit` is a complete lowercase 40-digit Git commit SHA, never an alias or
short SHA. Its value is the data version; `schema_version` is the protocol
version. The index has no self-hash or self-commit field. The `latest.json` file
at the data commit, if present, belongs to an earlier publication and must not
be followed recursively.

| Required index field | Meaning |
| --- | --- |
| `schema_version` | Integer `1` |
| `source_url` | Exactly `https://www.vpngate.net/api/iphone/` |
| `source_updated_at` | Reliable source directory update time, or `null`; currently `null` |
| `fetched_at` | Latest complete, successful, validated retrieval matching this snapshot |
| `generated_at` | When the current data files were generated; retained on identical content |
| `index_generated_at` | When this index was prepared before its commit/push |
| `data_commit` | Full data commit SHA |
| `source_record_count` | Number of source CSV records, including exact duplicates |
| `server_count` | Number of unique normalized nodes |
| `country_count` | Number of country-code buckets, including the unknown bucket if present |
| `files` | Descriptors for `data/vpngate.csv`, `data/servers.json`, `data/countries.json` |
| `workflow_run_url` | Public Actions run with publication result and visibility probes |

Each descriptor contains a lowercase 64-digit `sha256`, positive integer
`bytes`, and `server_count` equal to the top-level unique count. The CSV's
physical record count is `source_record_count`; the country file's group count
is `country_count`. File hashes cover exact stored bytes, including whitespace
and final newlines. Hash HTTP-decoded response bytes, not compressed wire bytes
or reformatted JSON.

All timestamps use UTC RFC 3339 with exactly three fractional digits, for example
`2026-09-11T12:17:42.123Z`. The index generation time is no earlier than the
fetch and data generation times. On an unchanged refresh, `generated_at` can
be older than `fetched_at`. Source HTTP `Date`, node uptime and local file times
are not source directory update timestamps. Actual `push_confirmed_at` and
per-endpoint `verified_at` observations belong to the Actions summary, not a
self-referential extra index commit.

## Raw CSV

`data/vpngate.csv` retains the original successful UTF-8 response bytes,
including optional BOM, line endings, all source columns, quoted values,
empty fields and both markers. `.gitattributes` disables text conversion for it.

Parsing requires the first record `*vpn_servers`, a marked header (the initial
`#` is removed only for parsing), and a final record containing only `*`.
Blank records are allowed between data records and after the end marker;
nonblank data after the marker fails. Header names must be unique and required
columns must exist. Column order may change, and extra columns remain in the
raw CSV. Every data record must have exactly the header's column count.
Python's strict CSV reader handles quoted commas, escaped quotes and quoted
multiline fields; splitting lines or splitting on commas is not equivalent.

Required columns: `HostName`, `IP`, `Score`, `Ping`, `Speed`, `CountryLong`,
`CountryShort`, `NumVpnSessions`, `OpenVPN_ConfigData_Base64`. Optional upstream
columns such as uptime, cumulative counters, operator and message are preserved
in CSV without inventing additional normalized types.

## Normalized nodes

`data/servers.json` contains `schema_version`, `source_csv_sha256`,
`server_count`, and `servers`. `source_csv_sha256` must match the manifest's raw
CSV hash. Nodes are sorted by ascending `id`.

| Node field | Type and convention |
| --- | --- |
| `id` | `v1:` followed by 64 lowercase SHA-256 digits |
| `hostname` | Original CSV `HostName` identifier, which may contain underscores; not necessarily a DNS name |
| `ip` | Canonical IPv4/IPv6 string; scoped, unspecified, multicast and loopback addresses fail |
| `country_code` | Source two-letter code uppercased, or `null`; non-two-letter values and `XX`/`ZZ` become unknown |
| `country_name` | Original nonempty source name or `null`; no geolocation lookup or renaming |
| `score` | Upstream score, nonnegative safe integer or `null`, no unit |
| `ping_ms` | Upstream Ping in milliseconds, nonnegative safe integer or `null` |
| `speed_bps` | Upstream speed in bits per second, nonnegative safe integer or `null` |
| `num_vpn_sessions` | Upstream session count, nonnegative safe integer or `null` |
| `openvpn_config_base64` | Complete original Base64 field, never truncated or re-encoded for storage |
| `openvpn_config_sha256` | SHA-256 of decoded original configuration bytes |
| `openvpn_config_bytes` | Length of decoded original configuration bytes |

Empty metric values become `null`; only Ping also accepts `-` as unknown. Zero
stays zero. Other nonnumeric values, negatives, fractions and numbers above
`9007199254740991` fail rather than silently becoming unknown. Unknown country
code originals remain available in CSV. These are upstream observations, not
local measurements, and there is no `online` or `verified_online` field.

`hostname` is stored unchanged. Validation and ID generation use its trimmed,
lowercase form with one final dot removed. Each dot-separated label has 1–63
ASCII letters, digits, underscores or hyphens, with no leading/trailing hyphen;
the normalized total is at most 253 characters (original field at most 254).
For example, upstream can return `_unregistered_vpn335506854`. Do not derive a
connection address or append a DNS suffix to this metadata. OpenVPN `remote`
destinations retain separate IP/DNS validation; TCP probes use verified numeric
IP/port targets only. Consumers copied before this correction must update their
hostname validator. The v1 JSON shape and normalization of existing IDs are unchanged.

ID input is the exact UTF-8 byte sequence:

```text
vpngate-node-v1<NUL>normalized_hostname<NUL>canonical_ip
```

The output is `v1:` plus its SHA-256 hex digest. Country, scores, ports and
configuration contents do not participate. A changed IP or hostname means a
new node ID. This is stable directory identity, not permanent device identity.
Identical complete CSV rows with the same ID are deduplicated. Any differing
row with the same ID fails the whole snapshot, including differences in extra
CSV columns, instead of selecting an arbitrary record.

Base64 validation is strict and canonical, including padding; decoded text
must be valid UTF-8 without control bytes other than CR/LF/tab. Basic structure
checks require `client`, a supported `dev`, a valid `remote` destination/port,
valid supplied transport directives, paired inline blocks and a CA block with
certificate delimiters. Optional client certificate/key blocks must appear
together. This is not a complete OpenVPN parser, certificate authentication,
security approval or a connection test. Other directives remain unmodified and
are never executed. Consumers save decoded bytes directly without newline or
encoding conversion.

## Country groups

`data/countries.json` contains `schema_version`, `source_csv_sha256`,
`server_count`, and `countries`. Each item has `code`, `names`, and
`server_count`. Names are unique original nonempty names sorted by Unicode code
point; conflicting names for the same code are retained in that array. The
unknown bucket has `code: null`; `names` can be empty, and a UI can display
“Unknown”. Known codes sort alphabetically; the unknown bucket is last.
The sum of all bucket counts equals the unique server count.

## Limits and validation

Source response: 8 MiB. Decoded configuration: 128 KiB (encoded field at most
174,764 characters). Records including duplicates: 5,000. Columns: 64. Each
published file: 16 MiB. Index consumption limit: 64 KiB. Fetching uses a
20-second network timeout, a 60-second response deadline checked between reads,
and at most three attempts for transient connection errors, 429 or 5xx. A read
already in progress can run until its socket timeout. Source requests ask for
identity encoding; unsupported compression fails explicitly. HTTPS redirects
must remain on the same host/standard port and are limited to two.

The complete response must validate. An empty list, missing end marker, invalid
required field, bad configuration or conflicting duplicate leaves the prior
published data and index untouched. Finishing a successful HTTP read alone
does not authorize publication. All three generated files are validated before
the single remote ref update.

Schemas are in [`schemas/v1`](../schemas/v1/). Runtime code explicitly validates
the corresponding constraints and cross-file semantics with the standard
library; it is not a general JSON Schema implementation. Consumers may use their
own JSON Schema validator, then still need to check hashes, counts and linkage.

## Compatibility and upgrades

Within `schema_version: 1`, optional fields may be added and unknown fields must
be ignored. Existing required fields, types, units, identity rules and meanings
stay compatible. The CSV remains an upstream format; new required semantics
must not be inferred silently from a changed upstream column.

For a breaking change, publish a new versioned directory and index (for example
`v2/latest.json` and `data/v2/`), update schemas and both consumers, and announce
at least 90 days of overlap before freezing v1. Frozen indexes retain their real
last-success timestamps. Unknown schema versions fail explicitly without
overwriting a consumer's previously verified data. Do not relabel old bytes as
a newer protocol or rewrite immutable data commits.
