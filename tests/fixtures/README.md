# Offline fixtures

These files are synthetic and contain documentation-range IP addresses and an
intentionally nonfunctional certificate. They are not usable VPN configurations.
The `up` line is a sentinel: parsers must preserve it as data and never execute it.
CSV files retain their original CRLF bytes. The quoted fixture also includes
quoted commas, escaped quotes, and embedded newlines.

`snapshot.json` is a small shared golden v1 snapshot for JavaScript consumers.
Its hashes and Base64 describe the exact synthetic files embedded in it. Its
commit SHA and workflow run URL are placeholders, never production endpoints.
