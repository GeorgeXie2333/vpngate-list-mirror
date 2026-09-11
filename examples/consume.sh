#!/usr/bin/env bash
set -euo pipefail

repo="${VPNGATE_REPO:-GeorgeXie2333/vpngate-list-mirror}"
country="${1:-JP}"
output="${2:-selected.ovpn}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
index_file="$(mktemp)"
trap 'rm -f -- "$index_file"' EXIT

curl --fail --silent --show-error --location --max-time 30 \
  --max-filesize 65536 --proto '=https' --proto-redir '=https' \
  "https://raw.githubusercontent.com/$repo/main/latest.json" --output "$index_file"
python3 "$script_dir/consume.py" --repo "$repo" --index-file "$index_file" \
  --country "$country" --output "$output"
