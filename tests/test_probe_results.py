import unittest
import uuid
from unittest.mock import Mock, patch

from mirror.probe_results import collect_batches
from mirror.pool import utc_round
from support import TIME


class ResultReadTests(unittest.TestCase):
    def test_http_errors_report_status_without_response_text_or_credentials(self):
        for status in (302, 401, 404, 500):
            with self.subTest(status=status):
                connection = Mock()
                connection.getresponse.return_value.status = status
                connection.getresponse.return_value.reason = "private-server-response"
                with patch("mirror.probe_results.http.client.HTTPSConnection", return_value=connection):
                    batches, report = collect_batches("https://probe.example", "private-token", TIME)
                self.assertFalse(batches)
                self.assertEqual(report["status"], "partial")
                self.assertEqual(report["errors"], [f"http_{status}"])
                connection.request.assert_called_once()
                connection.close.assert_called_once()

    def test_empty_kv_is_a_successful_read(self):
        def empty(url, token):
            return {"batches": [], "list_complete": True}
        batches, report = collect_batches("https://probe.example", "secret", TIME, read=empty)
        self.assertFalse(batches)
        self.assertEqual(report["status"], "read")
        self.assertEqual(report["errors"], [])

    def test_unconfigured_does_no_network(self):
        def forbidden(*args): raise AssertionError("Network must not run")
        self.assertEqual(collect_batches("","",TIME,read=forbidden)[1]["status"],"not_configured")

    def test_duplicates_processed_and_partial_failure(self):
        one,two=str(uuid.uuid4()),str(uuid.uuid4())
        number=utc_round(TIME)
        def read(url,token):
            self.assertEqual(token,"secret")
            if "/v1/batches?" in url:
                return {"batches":[{"key":f"results/{number}/{one}"},{"key":f"results/{number}/{two}"}] if f"round={number}&" in url else [],"list_complete":True}
            if url.endswith(two): raise TimeoutError()
            return {"batch_id":one}
        batches,report=collect_batches("https://probe.example","secret",TIME,read=read)
        self.assertEqual(batches,[{"batch_id":one}])
        self.assertEqual(report["status"],"partial")
        self.assertEqual(collect_batches("https://probe.example","secret",TIME,[one],read=read)[0],[])

    def test_bad_keys_do_not_become_arbitrary_requests(self):
        calls=[]
        def read(url,token):
            calls.append(url)
            return {"batches":[{"key":"https://evil.example/"}],"list_complete":True}
        batches,report=collect_batches("https://probe.example","secret",TIME,read=read)
        self.assertFalse(batches)
        self.assertEqual(len(calls),1)
        self.assertEqual(report["status"],"partial")
