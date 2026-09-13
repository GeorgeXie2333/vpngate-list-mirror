import base64
import csv
import io
import json
import unittest

from mirror import MAX_CONFIG_BYTES, MAX_SOURCE_BYTES, MirrorError
from mirror.parse import parse_csv
from mirror.snapshot import build_snapshot, parse_json
from mirror.validate import decode_config, node_id, sha256
from support import fixture, modified


class ParserTests(unittest.TestCase):
    def test_normal_csv_and_raw_bytes(self):
        raw = fixture()
        snapshot = build_snapshot(raw)
        self.assertEqual(snapshot.server_count, 2)
        self.assertEqual(snapshot.source_record_count, 2)
        self.assertEqual(snapshot.files["data/vpngate.csv"], raw)
        self.assertIn(b"\r\n", raw)

    def test_quoted_multiline_field(self):
        nodes, count = parse_csv(fixture("quoted-multiline.csv"))
        self.assertEqual(count, 2)
        self.assertTrue(any(node["country_name"] == 'Japan, "Example"\nRegion' for node in nodes))

    def test_nullable_fields_and_unknown_country(self):
        nodes, _ = parse_csv(fixture("nullable.csv"))
        node = next(node for node in nodes if node["hostname"] == "vpn-example")
        self.assertIsNone(node["country_code"])
        self.assertIsNone(node["country_name"])
        self.assertIsNone(node["ping_ms"])
        self.assertIsNone(node["score"])
        self.assertEqual(node["speed_bps"], 0)

    def test_id_stability_and_config_preservation(self):
        nodes, _ = parse_csv(fixture())
        changed, _ = parse_csv(modified(Score="200", Ping="0"))
        self.assertEqual([n["id"] for n in nodes], [n["id"] for n in changed])
        self.assertEqual(node_id("VPN-EXAMPLE.", "192.0.2.10"), node_id("vpn-example", "192.0.2.10"))
        self.assertNotEqual(node_id("vpn-example", "192.0.2.10"), node_id("vpn-example", "192.0.2.11"))
        config = base64.b64decode(nodes[0]["openvpn_config_base64"], validate=True)
        self.assertIn(b"up /this-command-must-never-run", config)
        self.assertEqual(decode_config(nodes[0]["openvpn_config_base64"]), config)

    def test_exact_duplicates_are_deduplicated(self):
        nodes, count = parse_csv(fixture("duplicate.csv"))
        self.assertEqual((len(nodes), count), (2, 3))

    def test_invalid_fixtures(self):
        for name in ["conflicting-duplicate.csv", "invalid-base64.csv", "truncated.csv", "empty.csv"]:
            with self.subTest(name=name), self.assertRaises(MirrorError):
                parse_csv(fixture(name))

    def test_missing_markers_headers_and_columns(self):
        raw = fixture()
        cases = [b"<html>error</html>", raw.replace(b"*vpn_servers", b"servers", 1),
                 raw.replace(b"#HostName", b"HostName", 1), raw.replace(b"#HostName", b"#Other", 1),
                 raw.replace(b",IP,", b",Score,", 1), raw + b"trailing garbage\r\n",
                 raw.replace(b",192.0.2.10,", b",192.0.2.10,extra,", 1)]
        for body in cases:
            with self.subTest(body=body[:50]), self.assertRaises(MirrorError):
                parse_csv(body)

    def test_bom_and_lf_are_accepted_but_preserved(self):
        for body in [b"\xef\xbb\xbf" + fixture(), fixture().replace(b"\r\n", b"\n")]:
            snapshot = build_snapshot(body)
            self.assertEqual(snapshot.files["data/vpngate.csv"], body)

    def test_bad_types_addresses_and_config_structure(self):
        values = [("Score", "NaN"), ("Ping", "-1"), ("Speed", "9007199254740992"),
                  ("NumVpnSessions", "1.5"), ("HostName", "bad/host"), ("IP", "999.1.1.1"),
                  ("IP", "127.0.0.1"), ("OpenVPN_ConfigData_Base64", "Y2xpZW50Cg==")]
        for key, value in values:
            with self.subTest(key=key, value=value), self.assertRaises(MirrorError):
                parse_csv(modified(**{key: value}))

    def test_hostname_diagnostic_escapes_values_and_counts_csv_records(self):
        value = 'bad\n::error::"host"\x1b\u202e'
        with self.assertRaises(MirrorError) as caught:
            parse_csv(modified(HostName=value))
        error = caught.exception
        self.assertEqual(str(error), "CSV record 1: Invalid hostname")
        detail = error.diagnostic
        self.assertEqual((detail["csv_record"], detail["csv_line_end"]), (1, 4))
        self.assertEqual(detail["field"], "HostName")
        self.assertEqual(json.loads(detail["value_preview"]), value)
        self.assertTrue(all(32 <= ord(c) < 127 for c in detail["value_preview"]))
        self.assertEqual(detail["value_characters"], len(value))
        self.assertFalse(detail["value_truncated"])
        self.assertEqual(detail["value_sha256"], sha256(value.encode()))

    def test_hostname_diagnostic_is_bounded_even_for_large_unicode_values(self):
        value = "\u4e2d" * 10000
        with self.assertRaises(MirrorError) as caught:
            parse_csv(modified(HostName=value))
        detail = caught.exception.diagnostic
        self.assertEqual(json.loads(detail["value_preview"]), value[:96])
        self.assertLessEqual(len(detail["value_preview"]), 96 * 6 + 2)
        self.assertTrue(detail["value_truncated"])
        self.assertEqual(detail["value_characters"], 10000)
        self.assertEqual(detail["value_sha256"], sha256(value.encode()))

    def test_remote_hostname_diagnostic_identifies_configuration_field(self):
        for connection_block in (False, True):
            with self.subTest(connection_block=connection_block):
                remote = "remote bad_host.example 443 tcp-client"
                if connection_block:
                    remote = "<connection>\n" + remote + "\n</connection>"
                config = ("client\ndev tun\n" + remote + "\n<ca>\n"
                          "-----BEGIN CERTIFICATE-----\nTEST-CERTIFICATE-MUST-NOT-BE-LOGGED\n"
                          "-----END CERTIFICATE-----\n</ca>\n").encode()
                encoded = base64.b64encode(config).decode()
                with self.assertRaises(MirrorError) as caught:
                    parse_csv(modified(OpenVPN_ConfigData_Base64=encoded))
                detail = caught.exception.diagnostic
                self.assertEqual(detail["field"], "OpenVPN_ConfigData_Base64.remote.host")
                self.assertEqual(json.loads(detail["value_preview"]), "bad_host.example")
                self.assertEqual(detail["csv_record"], 1)
                serialized = json.dumps(detail)
                self.assertNotIn("TEST-CERTIFICATE", serialized)
                self.assertNotIn(encoded, serialized)

    def test_limits_and_base64_padding(self):
        for body in [b"", b"x" * (MAX_SOURCE_BYTES + 1)]:
            with self.assertRaises(MirrorError):
                parse_csv(body)
        for encoded in ["YR==", "aGVsbG8=\n", base64.b64encode(b"x" * (MAX_CONFIG_BYTES + 1)).decode()]:
            with self.assertRaises(MirrorError):
                decode_config(encoded)

    def test_incomplete_inline_block(self):
        node = parse_csv(fixture())[0][0]
        body = base64.b64decode(node["openvpn_config_base64"]).replace(b"</ca>", b"</cert>")
        with self.assertRaises(MirrorError):
            decode_config(base64.b64encode(body).decode())

    def test_reordered_and_extra_columns(self):
        rows = list(csv.reader(io.StringIO(fixture().decode(), newline="")))
        rows[1][0] = rows[1][0][1:]
        for row in rows[1:-1]:
            row[0], row[1] = row[1], row[0]
            row.append("Extra" if row is rows[1] else "ignored but preserved")
        rows[1][0] = "#" + rows[1][0]
        output = io.StringIO(newline="")
        csv.writer(output).writerows(rows)
        self.assertEqual(len(parse_csv(output.getvalue().encode())[0]), 2)

    def test_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        for body in [b'{"a":1,"a":2}', b'{"value":NaN}']:
            with self.assertRaises(MirrorError):
                parse_json(body)
