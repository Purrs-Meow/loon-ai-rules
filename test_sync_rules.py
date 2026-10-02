"""Offline tests; these never contact the upstream service."""

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from http.client import IncompleteRead
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from scripts import sync_rules as sync


def source(count=101):
    return ("# Fixture\npayload:\n" + "".join(
        f"  - DOMAIN,host{i}.example.com\n" for i in range(count)
    )).encode()


def response(data=None, status=200, headers=None):
    result = MagicMock()
    result.status = status
    result.headers = headers or {}
    result.read.return_value = source() if data is None else data
    result.__enter__.return_value = result
    return result


class ParserTests(unittest.TestCase):
    def test_real_upstream_fixture_is_preserved_exactly(self):
        data = (Path(__file__).parent / "fixtures" / "Ai.yaml").read_bytes()
        lines, rules = sync.parse_source(data)
        expected = [line[4:] for line in data.decode().splitlines() if line.startswith("  - ")]
        self.assertEqual(rules, expected)
        self.assertEqual(len(rules), 101)
        self.assertEqual(Counter(r.split(",")[0] for r in rules), {"DOMAIN": 27, "DOMAIN-SUFFIX": 74})
        rendered = sync.render_output(lines)
        self.assertEqual(sync.read_existing_rules(rendered), expected)
        self.assertIn(b"# AUTHOR: https://t.me/ddgksf2021", rendered)
        self.assertIn(b"# > OpenAI/ChatGPT", rendered)
        self.assertNotIn(b"payload:", rendered)

    def test_quotes_inline_comments_and_case(self):
        data = b'''# title\npayload: # one list\n  - DOMAIN,MiXeD.example. # a comment\n  - 'DOMAIN-SUFFIX,example.com' # another\n  - "DOMAIN,api.example.com"\n'''
        _, rules = sync.parse_source(data)
        self.assertEqual(rules, ["DOMAIN,MiXeD.example.", "DOMAIN-SUFFIX,example.com", "DOMAIN,api.example.com"])

    def test_bom_and_windows_newlines(self):
        _, rules = sync.parse_source(b"\xef\xbb\xbfpayload:\r\n  - DOMAIN,a.example\r\n")
        self.assertEqual(rules, ["DOMAIN,a.example"])

    def test_duplicates_and_broad_rules_are_not_filtered(self):
        _, rules = sync.parse_source(b"payload:\n  - DOMAIN-SUFFIX,amazonaws.com\n  - DOMAIN-SUFFIX,cloudflare.com\n  - DOMAIN-SUFFIX,amazonaws.com\n")
        self.assertEqual(rules, ["DOMAIN-SUFFIX,amazonaws.com", "DOMAIN-SUFFIX,cloudflare.com", "DOMAIN-SUFFIX,amazonaws.com"])

    def test_rejects_invalid_source(self):
        cases = {
            "empty": b"",
            "whitespace": b" \n",
            "comments_only": b"# no rules\n",
            "no_payload": b"  - DOMAIN,a.example\n",
            "empty_payload": b"payload:\n# none\n",
            "duplicate_payload": b"payload:\n  - DOMAIN,a.example\npayload:\n  - DOMAIN,b.example\n",
            "no_space_payload_comment": b"payload:#not-a-comment\n  - DOMAIN,a.example\n",
            "wrong_key": b"rules:\n  - DOMAIN,a.example\n",
            "extra_key": b"payload:\n  - DOMAIN,a.example\nother: value\n",
            "html": b"<!DOCTYPE html><html><body>403</body></html>",
            "html_with_preamble": b"# error\n<html lang=\"en\">no</html>",
            "non_utf8": b"payload:\n  - DOMAIN,\xff.example\n",
            "tab": b"payload:\n\t- DOMAIN,a.example\n",
            "nul": b"payload:\n  - DOMAIN,a.example\x00\n",
            "unknown_type": b"payload:\n  - DOMAIN-KEYWORD,openai\n",
            "non_rule": b"payload:\n  - true\n",
            "ip_rule": b"payload:\n  - IP-CIDR,1.2.3.4/32\n",
            "policy_suffix": b"payload:\n  - DOMAIN,a.example,PROXY\n",
            "nested_list": b"payload:\n    - DOMAIN,a.example\n",
            "unindented_list": b"payload:\n- DOMAIN,a.example\n",
            "mapping": b"payload:\n  - {DOMAIN: a.example}\n",
            "flow_list": b"payload: [DOMAIN,a.example]\n",
            "tag": b"payload:\n  - !!str DOMAIN,a.example\n",
            "anchor": b"payload:\n  - &first DOMAIN,a.example\n",
            "alias": b"payload:\n  - *first\n",
            "multiline_scalar": b"payload:\n  - |\n    DOMAIN,a.example\n",
            "empty_domain": b"payload:\n  - DOMAIN,\n",
            "wildcard": b"payload:\n  - DOMAIN,*.example.com\n",
            "url": b"payload:\n  - DOMAIN,https://example.com\n",
            "whitespace_domain": b"payload:\n  - DOMAIN, a.example\n",
            "empty_label": b"payload:\n  - DOMAIN,a..example\n",
            "hyphen_label": b"payload:\n  - DOMAIN,-a.example\n",
            "leading_dot": b"payload:\n  - DOMAIN-SUFFIX,.example.com\n",
            "long_label": b"payload:\n  - DOMAIN," + b"a" * 64 + b".example\n",
            "unterminated_quote": b'payload:\n  - "DOMAIN,a.example\n',
            "trailing_quoted_data": b'payload:\n  - "DOMAIN,a.example" garbage\n',
            "no_space_quoted_comment": b'payload:\n  - "DOMAIN,a.example"#comment\n',
            "quoted_newline": b'payload:\n  - "DOMAIN,a.example\\nDOMAIN,b.example"\n',
            "unquoted_nbsp": "payload:\n  - DOMAIN,a.example\u00a0\n".encode(),
            "nbsp_before_comment": "payload:\n  - DOMAIN,a.example\u00a0 # comment\n".encode(),
            "unicode_comment_separator": "payload:\n# a comment\u2028DOMAIN,b.example\n  - DOMAIN,a.example\n".encode(),
            "second_document": b"payload:\n  - DOMAIN,a.example\n---\npayload:\n  - DOMAIN,b.example\n",
            "oversized": b"#" * (sync.MAX_BYTES + 1),
        }
        for name, data in cases.items():
            with self.subTest(name=name), self.assertRaises(sync.SyncError):
                sync.parse_source(data)

    def test_html_content_type_is_rejected_even_with_plausible_body(self):
        with self.assertRaises(sync.SyncError):
            sync.decode_source(source(), "text/html; charset=utf-8")

    def test_count_thresholds(self):
        sync.validate_counts(50, None)
        sync.validate_counts(75, 100)
        sync.validate_counts(101, 101)
        sync.validate_counts(300, 101)
        for current, previous in [(49, None), (74, 100), (75, 101), (0, 101)]:
            with self.subTest(current=current, previous=previous), self.assertRaises(sync.SyncError):
                sync.validate_counts(current, previous)


class FileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "Ai.lsr"
        self.assertTrue(sync.sync(source(), self.output)[0])
        self.original = self.output.read_bytes()

    def test_unchanged_data_never_rewrites_file(self):
        original_mtime = self.output.stat().st_mtime_ns
        with patch.object(sync, "atomic_write") as write:
            changed, counts = sync.sync(source(), self.output)
        self.assertFalse(changed)
        self.assertEqual(counts, {"DOMAIN": 101})
        write.assert_not_called()
        self.assertEqual(self.output.read_bytes(), self.original)
        self.assertEqual(self.output.stat().st_mtime_ns, original_mtime)

    def test_valid_update_is_atomic_and_has_stable_header(self):
        self.assertTrue(sync.sync(source(102), self.output)[0])
        self.assertEqual(len(sync.read_existing_rules(self.output.read_bytes())), 102)
        self.assertTrue(self.output.read_bytes().startswith(sync.HEADER.encode()))
        self.assertEqual(list(Path(self.directory.name).glob(".Ai.lsr.*")), [])

    def test_invalid_input_never_replaces_existing_output(self):
        for bad in [b"", b"<html>403</html>", source(49), source(75), b"payload:\n  - MATCH,PROXY\n"]:
            with self.subTest(bad=bad[:40]), self.assertRaises(sync.SyncError):
                sync.sync(bad, self.output)
            self.assertEqual(self.output.read_bytes(), self.original)

    def test_replace_failure_preserves_original_and_cleans_temp(self):
        with patch.object(sync.os, "replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
            sync.sync(source(102), self.output)
        self.assertEqual(self.output.read_bytes(), self.original)
        self.assertEqual(list(Path(self.directory.name).glob(".Ai.lsr.*")), [])

    def test_flush_failure_preserves_original_and_cleans_temp(self):
        with patch.object(sync.os, "fsync", side_effect=OSError("disk error")), self.assertRaises(OSError):
            sync.sync(source(102), self.output)
        self.assertEqual(self.output.read_bytes(), self.original)
        self.assertEqual(list(Path(self.directory.name).glob(".Ai.lsr.*")), [])

    def test_corrupt_existing_output_fails_closed(self):
        self.output.write_bytes(b"<html>error</html>")
        with self.assertRaises(sync.SyncError):
            sync.sync(source(), self.output)
        self.assertEqual(self.output.read_bytes(), b"<html>error</html>")

    def test_first_run_invalid_data_does_not_create_output(self):
        output = self.output.with_name("missing.lsr")
        with self.assertRaises(sync.SyncError):
            sync.sync(source(1), output)
        self.assertFalse(output.exists())

    def test_generated_rule_mismatch_does_not_replace_output(self):
        with patch.object(sync, "render_output", return_value=b"DOMAIN,b.example\n"), self.assertRaises(sync.SyncError):
            sync.sync(source(), self.output)
        self.assertEqual(self.output.read_bytes(), self.original)

    def test_cli_failure_returns_nonzero_and_retains_output(self):
        with patch.object(sync, "fetch_source", side_effect=sync.SyncError("HTTP 403")), redirect_stderr(StringIO()) as stderr:
            self.assertEqual(sync.main(["--output", str(self.output)]), 1)
        self.assertIn("HTTP 403", stderr.getvalue())
        self.assertEqual(self.output.read_bytes(), self.original)

    def test_truncated_http_body_retains_output(self):
        result = response()
        result.read.side_effect = IncompleteRead(b"payload:\n", 1000)
        with patch.object(sync, "build_opener") as factory, patch.object(sync.time, "sleep"), redirect_stderr(StringIO()) as stderr:
            factory.return_value.open.return_value = result
            self.assertEqual(sync.main(["--output", str(self.output)]), 1)
            self.assertEqual(factory.return_value.open.call_count, 3)
        self.assertIn("IncompleteRead", stderr.getvalue())
        self.assertEqual(self.output.read_bytes(), self.original)

    def test_cli_offline_conversion(self):
        path = self.output.with_name("source.yaml")
        path.write_bytes(source(102))
        with redirect_stdout(StringIO()) as stdout:
            self.assertEqual(sync.main(["--input", str(path), "--output", str(self.output)]), 0)
        self.assertIn("102 rules", stdout.getvalue())


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.factory = patch.object(sync, "build_opener").start()
        self.addCleanup(patch.stopall)
        self.opener = self.factory.return_value
        self.sleep = patch.object(sync.time, "sleep").start()

    def test_success_uses_bounded_read_timeout_and_honest_user_agent(self):
        result = response(headers={"Content-Type": "text/plain", "Content-Length": str(len(source()))})
        self.opener.open.return_value = result
        self.assertEqual(sync.fetch_source(), source())
        result.read.assert_called_once_with(sync.MAX_BYTES + 1)
        args, kwargs = self.opener.open.call_args
        self.assertEqual(kwargs["timeout"], 25)
        self.assertIn("loon-ai-rules", args[0].get_header("User-agent"))
        self.sleep.assert_not_called()

    def test_403_is_not_retried_or_treated_as_rules(self):
        self.opener.open.side_effect = HTTPError(sync.SOURCE_URL, 403, "Forbidden", {}, None)
        with self.assertRaisesRegex(sync.SyncError, "HTTP 403"):
            sync.fetch_source()
        self.assertEqual(self.opener.open.call_count, 1)
        self.sleep.assert_not_called()

    def test_transient_http_error_is_retried(self):
        self.opener.open.side_effect = [HTTPError(sync.SOURCE_URL, 503, "Unavailable", {}, None), response()]
        self.assertEqual(sync.fetch_source(), source())
        self.assertEqual(self.opener.open.call_count, 2)
        self.sleep.assert_called_once_with(3)

    def test_network_failure_is_bounded(self):
        self.opener.open.side_effect = URLError("connection unavailable")
        with self.assertRaises(sync.SyncError):
            sync.fetch_source()
        self.assertEqual(self.opener.open.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_timeout_is_retried(self):
        self.opener.open.side_effect = [TimeoutError("timeout"), response()]
        self.assertEqual(sync.fetch_source(), source())
        self.assertEqual(self.opener.open.call_count, 2)

    def test_truncated_http_body_is_retried(self):
        result = response()
        result.read.side_effect = IncompleteRead(b"payload:\n", 1000)
        self.opener.open.side_effect = [result, response()]
        self.assertEqual(sync.fetch_source(), source())
        self.assertEqual(self.opener.open.call_count, 2)

    def test_bad_responses_fail_without_retry(self):
        cases = [
            response(status=204),
            response(data=b"<html>blocked</html>"),
            response(headers={"Content-Type": "text/html"}),
            response(data=b""),
            response(data=b"x" * (sync.MAX_BYTES + 1)),
            response(headers={"Content-Length": str(sync.MAX_BYTES + 1)}),
            response(headers={"Content-Length": "not-a-number"}),
            response(headers={"Content-Length": "123"}),
        ]
        for result in cases:
            with self.subTest(status=result.status, headers=result.headers):
                self.opener.open.reset_mock()
                self.opener.open.return_value = result
                with self.assertRaises(sync.SyncError):
                    sync.fetch_source()
                self.assertEqual(self.opener.open.call_count, 1)

    def test_insecure_or_credential_bearing_source_is_rejected(self):
        for url in ["http://example.com/rules", "https://user:secret@example.com/rules", "file:///tmp/rules"]:
            with self.subTest(url=url), self.assertRaises(sync.SyncError):
                sync.fetch_source(url)
        self.opener.open.assert_not_called()

    def test_insecure_redirect_is_rejected(self):
        for url in ["http://example.com/rules", "https://user:secret@example.com/rules"]:
            with self.subTest(url=url), self.assertRaises(sync.SyncError):
                sync.HTTPSOnlyRedirect().redirect_request(None, None, 302, "Found", {}, url)


if __name__ == "__main__":
    unittest.main()
