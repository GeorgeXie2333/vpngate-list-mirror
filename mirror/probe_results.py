"""Read completed KV batches over an authenticated, fixed HTTPS origin only."""

import http.client
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import MirrorError
from .pool import BATCH, require, utc_round
from .snapshot import parse_json

MAX_BATCH = 65536


class ProbeHTTPError(MirrorError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Probe reader HTTP {status}")


def _error_code(error):
    # Report only the status, never remote response text, URLs or credentials.
    return f"http_{error.status}" if isinstance(error, ProbeHTTPError) else type(error).__name__


def read_result(url, token, limit=MAX_BATCH):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and not parsed.fragment, "Invalid results URL")
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=5)
    try:
        # Never follow a redirect carrying this credential.
        connection.request("GET", urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, "")),
                           headers={"Authorization": f"Bearer {token}", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200:
            raise ProbeHTTPError(response.status)
        require(response.getheader("Content-Encoding", "identity") == "identity", "Encoded result response")
        deadline, body = time.monotonic() + 5, bytearray()
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, "Probe response deadline exceeded")
            if connection.sock:
                connection.sock.settimeout(remaining)
            part = response.read1(min(8192, limit + 1 - len(body)))
            if not part:
                break
            body.extend(part)
            require(len(body) <= limit, "Oversized result response")
        require(len(body) <= limit, "Oversized result response")
        return parse_json(bytes(body))
    finally:
        connection.close()


def collect_batches(base_url, token, now, processed=(), *, read=read_result):
    if not base_url or not token:
        return [], {"status": "not_configured", "batches_read": 0}
    started, batches, errors, keys = time.monotonic(), [], [], set()
    try:
        parsed = urllib.parse.urlsplit(base_url)
        require(parsed.scheme == "https" and parsed.hostname and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and parsed.path in ("", "/")
                and not parsed.query and not parsed.fragment, "Reader must be a fixed HTTPS origin")
        origin = base_url.rstrip("/")
        for number in (utc_round(now) - 1, utc_round(now)):
            cursor = ""
            for _ in range(4):
                if time.monotonic() - started >= 15:
                    errors.append("read_budget")
                    break
                page = read(f"{origin}/v1/batches?round={number}&cursor={urllib.parse.quote(cursor, safe='')}", token)
                require(isinstance(page, dict) and isinstance(page.get("batches"), list), "Invalid batch listing")
                for item in page["batches"]:
                    key = item.get("key", "")
                    parts = key.split("/")
                    require(len(parts) == 3 and parts[:2] == ["results", str(number)]
                            and BATCH.fullmatch(parts[2]), "Invalid batch key")
                    if parts[2] not in processed:
                        keys.add(key)
                if page.get("list_complete") is True:
                    break
                cursor = page.get("cursor")
                require(isinstance(cursor, str) and len(cursor) <= 2048, "Invalid KV cursor")
                if time.monotonic() - started >= 15:
                    break
        def one(key):
            if time.monotonic() - started >= 15:
                return None, "read_budget"
            try:
                value = read(f"{origin}/v1/batch/{key}", token)
                require(isinstance(value, dict) and value.get("batch_id") == key.split("/")[-1], "Mismatched batch key")
                return value, None
            except (OSError, http.client.HTTPException, MirrorError, ValueError) as exc:
                return None, _error_code(exc)
        with ThreadPoolExecutor(max_workers=4) as executor:
            for value, error in executor.map(one, sorted(keys)[:300]):
                if value is not None:
                    batches.append(value)
                if error:
                    errors.append(error)
    except (OSError, http.client.HTTPException, MirrorError, ValueError, TypeError, AttributeError) as exc:
        errors.append(_error_code(exc))
    return batches, {"status": "partial" if errors else "read", "batches_read": len(batches),
                     "pending": max(0, len(keys) - len(batches)), "errors": errors[:5]}
