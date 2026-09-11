import io
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from mirror import SOURCE_URL, MirrorError
from mirror.fetch import SameHostHTTPSRedirect, fetch_source, read_https


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


class FetchTests(unittest.TestCase):
    def test_bounded_complete_response(self):
        opener = Mock()
        opener.open.return_value = Response(b"abc", {"Content-Length": "3"})
        self.assertEqual(read_https(SOURCE_URL, 3, opener=opener), b"abc")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Accept-encoding"), "identity")

    def test_size_length_and_encoding_errors(self):
        cases = [(b"abcd", {}), (b"ab", {"Content-Length": "3"}),
                 (b"abc", {"Content-Length": "100"}), (b"abc", {"Content-Length": "NaN"}),
                 (b"abc", {"Content-Encoding": "gzip"})]
        for body, headers in cases:
            with self.subTest(headers=headers, body=body):
                opener = Mock()
                opener.open.return_value = Response(body, headers)
                with self.assertRaises(MirrorError):
                    read_https(SOURCE_URL, 3, opener=opener)

    def test_timeout_retries_are_finite(self):
        read, sleep = Mock(side_effect=TimeoutError()), Mock()
        with self.assertRaisesRegex(MirrorError, "3 attempts"):
            fetch_source(read=read, sleep=sleep)
        self.assertEqual(read.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 3])

    def test_success_uses_one_response_not_failed_fragments(self):
        read = Mock(side_effect=[TimeoutError(), b"complete"])
        body, timestamp = fetch_source(read=read, sleep=Mock())
        self.assertEqual(body, b"complete")
        self.assertTrue(timestamp.endswith("Z"))

    def test_http_status_retry_policy(self):
        for code, expected in [(429, 3), (503, 3), (404, 1)]:
            read = Mock(side_effect=urllib.error.HTTPError(SOURCE_URL, code, "error", {}, None))
            with self.assertRaises(MirrorError):
                fetch_source(read=read, sleep=Mock())
            self.assertEqual(read.call_count, expected)

    def test_validation_failure_is_not_retried(self):
        read = Mock(side_effect=MirrorError("oversized"))
        with self.assertRaises(MirrorError):
            fetch_source(read=read, sleep=Mock())
        self.assertEqual(read.call_count, 1)

    def test_response_deadline(self):
        opener = Mock()
        opener.open.return_value = Response(b"abc")
        with patch("mirror.fetch.time.monotonic", side_effect=[0, 61]):
            with self.assertRaises(TimeoutError):
                read_https(SOURCE_URL, 3, opener=opener)

    def test_only_same_host_https_redirects(self):
        request = urllib.request.Request(SOURCE_URL)
        handler = SameHostHTTPSRedirect()
        for target in ["http://www.vpngate.net/api/iphone/", "https://example.com/", "https://www.vpngate.net:8443/"]:
            with self.assertRaises(MirrorError):
                handler.redirect_request(request, None, 302, "redirect", {}, target)
