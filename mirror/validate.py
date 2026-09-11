"""Explicit validation of source fields and v1 manifests."""

import base64
import binascii
import hashlib
import ipaddress
import re
import shlex
from datetime import datetime

from . import (DATA_PATHS, MAX_CONFIG_BYTES, MAX_FILE_BYTES, MAX_SAFE_INTEGER,
               MAX_SERVERS, SCHEMA_VERSION, SOURCE_URL, MirrorError)

SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
PROTOCOLS = {"udp", "udp4", "udp6", "tcp", "tcp4", "tcp6", "tcp-client", "tcp4-client", "tcp6-client"}


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def hostname(value):
    if len(value) > 254:
        raise MirrorError("Hostname exceeds size limit")
    normalized = value.strip().lower().removesuffix(".")
    if not normalized or len(normalized) > 253 or any(not LABEL.fullmatch(x) for x in normalized.split(".")):
        raise MirrorError("Invalid hostname")
    return normalized


def address(value):
    try:
        if "%" in value:
            raise ValueError("Scoped address")
        parsed = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise MirrorError("Invalid IP address") from exc
    if parsed.is_unspecified or parsed.is_multicast or parsed.is_loopback:
        raise MirrorError("Invalid unicast server address")
    return str(parsed)


def node_id(host, ip):
    return "v1:" + sha256(("vpngate-node-v1\0" + hostname(host) + "\0" + address(ip)).encode("utf-8"))


def nullable_integer(value, field):
    value = value.strip()
    if value == "" or (field == "Ping" and value == "-"):
        return None
    if not re.fullmatch(r"[0-9]+", value) or len(value) > 16:
        raise MirrorError(f"Invalid numeric field: {field}")
    result = int(value)
    if result > MAX_SAFE_INTEGER:
        raise MirrorError(f"Numeric field exceeds safe integer range: {field}")
    return result


def decode_config(encoded):
    if not isinstance(encoded, str) or not encoded or len(encoded) > 4 * ((MAX_CONFIG_BYTES + 2) // 3):
        raise MirrorError("Missing or oversized OpenVPN configuration")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise MirrorError("Invalid configuration Base64") from exc
    if not body or len(body) > MAX_CONFIG_BYTES or base64.b64encode(body).decode("ascii") != encoded:
        raise MirrorError("Noncanonical or oversized configuration Base64")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MirrorError("Configuration is not UTF-8 text") from exc
    if any(ord(c) < 32 and c not in "\r\n\t" for c in text):
        raise MirrorError("Configuration contains control bytes")
    block = None
    content = []
    blocks = {}
    client = dev = False
    remotes = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if block:
            if line == f"</{block}>":
                if not content:
                    raise MirrorError("Empty inline configuration block")
                blocks[block] = "\n".join(content)
                block, content = None, []
            elif re.fullmatch(r"</?[A-Za-z0-9_-]+>", line):
                raise MirrorError("Mismatched inline configuration block")
            else:
                content.append(line)
            continue
        tag = re.fullmatch(r"<([A-Za-z0-9_-]+)>", line)
        if tag:
            block = tag.group(1)
            if block in blocks:
                raise MirrorError("Duplicate inline configuration block")
            continue
        if line.startswith("</"):
            raise MirrorError("Unexpected closing configuration block")
        # Only tokenize the directives whose basic syntax we validate. Other
        # options (including script directives) are preserved, never executed.
        key = line.split(None, 1)[0]
        if key not in {"client", "dev", "proto", "remote"}:
            continue
        try:
            parts = shlex.split(line, comments=True)
        except ValueError as exc:
            raise MirrorError("Malformed configuration directive") from exc
        if key == "client":
            client = len(parts) == 1
        elif key == "dev":
            dev = len(parts) == 2 and re.fullmatch(r"(?:tun|tap)[0-9]*", parts[1]) is not None
        elif key == "proto":
            if len(parts) != 2 or parts[1] not in PROTOCOLS:
                raise MirrorError("Invalid OpenVPN protocol")
        elif key == "remote":
            if not 2 <= len(parts) <= 4:
                raise MirrorError("Invalid remote directive")
            try:
                address(parts[1])
            except MirrorError:
                if re.fullmatch(r"[0-9.]+", parts[1]) or ":" in parts[1]:
                    raise MirrorError("Invalid remote IP address")
                hostname(parts[1])
            if len(parts) >= 3 and (not re.fullmatch(r"[0-9]{1,5}", parts[2]) or not 1 <= int(parts[2]) <= 65535):
                raise MirrorError("Invalid remote port")
            if len(parts) == 4 and parts[3] not in PROTOCOLS:
                raise MirrorError("Invalid remote protocol")
            remotes += 1
    if block or not (client and dev and remotes):
        raise MirrorError("Incomplete OpenVPN client configuration")
    if "ca" not in blocks or "-----BEGIN CERTIFICATE-----" not in blocks["ca"] or "-----END CERTIFICATE-----" not in blocks["ca"]:
        raise MirrorError("Missing inline CA certificate structure")
    if ("cert" in blocks) != ("key" in blocks):
        raise MirrorError("Incomplete inline client certificate/key pair")
    return body


def normalize_record(record):
    host = record["HostName"]
    hostname(host)
    ip = address(record["IP"])
    code = record["CountryShort"].strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", code) or code in {"ZZ", "XX"}:
        code = None
    config = decode_config(record["OpenVPN_ConfigData_Base64"])
    return {
        "id": node_id(host, ip), "hostname": host, "ip": ip,
        "country_code": code, "country_name": record["CountryLong"] or None,
        "score": nullable_integer(record["Score"], "Score"),
        "ping_ms": nullable_integer(record["Ping"], "Ping"),
        "speed_bps": nullable_integer(record["Speed"], "Speed"),
        "num_vpn_sessions": nullable_integer(record["NumVpnSessions"], "NumVpnSessions"),
        "openvpn_config_base64": record["OpenVPN_ConfigData_Base64"],
        "openvpn_config_sha256": sha256(config), "openvpn_config_bytes": len(config),
    }


def parse_utc(value):
    if not isinstance(value, str) or not UTC.fullmatch(value):
        raise MirrorError("Timestamp must be UTC with millisecond precision")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MirrorError("Invalid timestamp") from exc


def integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise MirrorError(f"Invalid {label}")


def validate_index(index):
    if not isinstance(index, dict) or type(index.get("schema_version")) is not int or index["schema_version"] != SCHEMA_VERSION:
        raise MirrorError("Unsupported schema_version")
    if index.get("source_url") != SOURCE_URL:
        raise MirrorError("Unexpected source_url")
    if "source_updated_at" not in index:
        raise MirrorError("Missing source_updated_at")
    if index["source_updated_at"] is not None:
        parse_utc(index["source_updated_at"])
    times = {key: parse_utc(index.get(key)) for key in ("fetched_at", "generated_at", "index_generated_at")}
    if times["index_generated_at"] < max(times["fetched_at"], times["generated_at"]):
        raise MirrorError("Inconsistent snapshot timestamps")
    if not isinstance(index.get("data_commit"), str) or not COMMIT.fullmatch(index["data_commit"]):
        raise MirrorError("Invalid full data commit SHA")
    integer(index.get("server_count"), 1, MAX_SERVERS, "server_count")
    integer(index.get("source_record_count"), index["server_count"], MAX_SERVERS, "source_record_count")
    integer(index.get("country_count"), 1, index["server_count"], "country_count")
    if not isinstance(index.get("workflow_run_url"), str) or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+", index["workflow_run_url"]):
        raise MirrorError("Invalid workflow_run_url")
    files = index.get("files")
    if not isinstance(files, dict):
        raise MirrorError("Missing file manifest")
    for path in DATA_PATHS:
        meta = files.get(path)
        if not isinstance(meta, dict) or not isinstance(meta.get("sha256"), str) or not SHA256.fullmatch(meta["sha256"]):
            raise MirrorError(f"Invalid file hash: {path}")
        integer(meta.get("bytes"), 1, MAX_FILE_BYTES, "file size")
        if type(meta.get("server_count")) is not int or meta["server_count"] != index["server_count"]:
            raise MirrorError("Inconsistent manifest node counts")
    return index
