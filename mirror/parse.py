"""Parse CSV records without modifying the original response bytes."""

import csv
import io

from . import MAX_SERVERS, MAX_SOURCE_BYTES, MirrorError
from .validate import normalize_record

REQUIRED = {"HostName", "IP", "Score", "Ping", "Speed", "CountryLong", "CountryShort", "NumVpnSessions", "OpenVPN_ConfigData_Base64"}


def parse_csv(body):
    if not body or len(body) > MAX_SOURCE_BYTES:
        raise MirrorError("Empty or oversized CSV response")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MirrorError("CSV is not UTF-8") from exc
    csv.field_size_limit(MAX_SOURCE_BYTES)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        if next(reader, None) != ["*vpn_servers"]:
            raise MirrorError("Missing CSV start marker")
        header = next(reader, None)
        if not header or not header[0].startswith("#"):
            raise MirrorError("Missing CSV header marker")
        header[0] = header[0][1:]
        if len(header) > 64 or len(set(header)) != len(header) or any(not x or len(x) > 256 for x in header):
            raise MirrorError("Invalid or duplicate CSV columns")
        if not REQUIRED.issubset(header):
            raise MirrorError("Missing required CSV columns")
        records = {}
        original_rows = {}
        row_count = 0
        ended = False
        for row in reader:
            if not row:
                continue
            if ended:
                raise MirrorError("Unexpected records after CSV end marker")
            if row == ["*"]:
                ended = True
                continue
            row_count += 1
            if row_count > MAX_SERVERS or len(row) != len(header):
                raise MirrorError(f"Invalid CSV row shape/count at record {row_count}")
            try:
                normalized = normalize_record(dict(zip(header, row)))
            except MirrorError as exc:
                raise MirrorError(f"CSV record {row_count}: {exc}") from exc
            key = normalized["id"]
            if key in original_rows and original_rows[key] != row:
                raise MirrorError(f"Conflicting duplicate node at record {row_count}")
            original_rows[key] = row
            records[key] = normalized
        if not ended:
            raise MirrorError("Missing CSV end marker (possibly truncated response)")
        if not records:
            raise MirrorError("Empty server directory")
        return sorted(records.values(), key=lambda x: x["id"]), row_count
    except csv.Error as exc:
        raise MirrorError("Malformed quoted CSV") from exc
