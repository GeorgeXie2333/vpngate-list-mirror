"""Bounded, TLS-verified HTTPS reads. No VPN connections are made."""

import http.client
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import MAX_SOURCE_BYTES, SOURCE_URL, MirrorError


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SameHostHTTPSRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 2
    max_repeats = 2

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(request.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (new.scheme != "https" or new.hostname != old.hostname
                or new.port not in (None, 443) or new.username or new.password):
            raise MirrorError("Redirect left the original HTTPS host")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def read_https(url, limit, *, timeout=20, opener=None, deadline_seconds=60):
    """Read at most limit bytes; Content-Length is checked when present."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise MirrorError("Only anonymous HTTPS URLs are supported")
    opener = opener or urllib.request.build_opener(SameHostHTTPSRedirect())
    request = urllib.request.Request(url, headers={
        "User-Agent": "vpngate-list-mirror/1 (+https://github.com/GeorgeXie2333/vpngate-list-mirror)",
        "Accept-Encoding": "identity",
    })
    started = time.monotonic()
    with opener.open(request, timeout=timeout) as response:
        if response.status != 200:
            raise MirrorError(f"Unexpected HTTP status: {response.status}")
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding not in ("", "identity"):
            raise MirrorError("Unexpected content encoding")
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                length = int(length)
            except ValueError as exc:
                raise MirrorError("Invalid Content-Length") from exc
            if length < 0 or length > limit:
                raise MirrorError("Response exceeds size limit")
        body = bytearray()
        # read1 performs a bounded socket read, allowing deadline checks even
        # when a peer sends a slow stream. Each read also has the socket timeout.
        read = getattr(response, "read1", response.read)
        while True:
            if time.monotonic() - started > deadline_seconds:
                raise TimeoutError("Response deadline exceeded")
            chunk = read(min(65536, limit + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > limit:
                raise MirrorError("Response exceeds size limit")
        if length is not None and len(body) != length:
            raise MirrorError("Truncated HTTP response")
    return bytes(body)


def fetch_source(*, read=read_https, sleep=time.sleep, attempts=3):
    for attempt in range(attempts):
        try:
            body = read(SOURCE_URL, MAX_SOURCE_BYTES)
            return body, utc_now()
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code <= 599:
                raise MirrorError(f"Source returned HTTP {exc.code}") from exc
            error = exc
        except (urllib.error.URLError, TimeoutError, socket.timeout,
                ConnectionError, http.client.IncompleteRead) as exc:
            error = exc
        if attempt + 1 < attempts:
            sleep((1, 3)[min(attempt, 1)])
    raise MirrorError(f"Source request failed after {attempts} attempts: {type(error).__name__}") from error
