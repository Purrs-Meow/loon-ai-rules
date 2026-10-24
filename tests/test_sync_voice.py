"""Offline Loon-format, coverage and failure-safety checks for voice IP rules."""
from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sync_voice as voice

FIXTURE = Path(__file__).parent / "fixtures" / "chatgpt-voice.json"


def source(prefixes):
    return json.dumps({"creationTime": "2026-10-24T00:00:00Z", "prefixes": prefixes}).encode()


class VoiceTests(unittest.TestCase):
    def test_official_snapshot_and_loon_format(self):
        data = FIXTURE.read_bytes()
        expected = {p["ipv4Prefix"] for p in json.loads(data)["prefixes"]}
        nets = voice.parse_source(data)
        self.assertEqual({str(n) for n in nets}, expected)
        self.assertEqual(len(nets), 23)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            changed, count = voice.sync(data, root)
            self.assertEqual(changed, [voice.LSR])
            self.assertEqual(count, 23)
            self.assertEqual([p.name for p in root.iterdir()], [voice.LSR])
            output = (root / voice.LSR).read_bytes()
            self.assertEqual(voice.read_rules(output), nets)
            self.assertIn(b"# FORMAT: Loon remote rule list (.lsr)", output)
            self.assertNotIn(b"payload:", output)
            rules = [r for r in output.decode().splitlines() if r and not r.startswith("#")]
            self.assertEqual(rules, [f"IP-CIDR,{n},no-resolve" for n in nets])

    def test_ipv6_family_and_stable_order(self):
        data = source([{"ipv6Prefix": "2606:4700::1111/128"}, {"ipv4Prefix": "8.8.8.8/32"}])
        nets = voice.parse_source(data)
        self.assertEqual([n.version for n in nets], [4, 6])
        rendered = voice.render_output(nets)
        self.assertIn(b"IP-CIDR6,2606:4700::1111/128,no-resolve", rendered)
        self.assertEqual(voice.read_rules(rendered), nets)

    def test_bad_schema_and_unsafe_prefixes_fail(self):
        cases = [b"", b"<html>error</html>", b"[]", b"{}", b'{"creationTime":1,"prefixes":[]}',
                 b'{"creationTime":"x","creationTime":"y","prefixes":[]}',
                 source([]), source([{"unknown": "8.8.8.8/32"}]),
                 source([{"ipv4Prefix": "8.8.8.8/32", "extra": True}]),
                 source([{"ipv4Prefix": "8.8.8.8/32"}] * 2),
                 source([{"ipv4Prefix": "8.8.8.8/32"}] * 1025)]
        for value in ["0.0.0.0/0", "10.0.0.1/32", "127.0.0.1/32",
                      "169.254.1.1/32", "224.0.0.1/32", "8.0.0.0/8",
                      "8.8.8.1/24", "8.8.8.8", "8.8.8.8/32,REJECT",
                      "2606:4700::1111/128", None]:
            cases.append(source([{"ipv4Prefix": value}]))
        for data in cases:
            with self.subTest(data=data[:80]), self.assertRaises(voice.SyncError):
                voice.parse_source(data)

    def test_invalid_source_or_large_drop_preserves_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            voice.sync(FIXTURE.read_bytes(), root)
            before = (root / voice.LSR).read_bytes()
            for data in [b"<html>error</html>", source([{"ipv4Prefix": "8.8.8.8/32"}])]:
                with self.assertRaises(voice.SyncError):
                    voice.sync(data, root)
                self.assertEqual(before, (root / voice.LSR).read_bytes())

    def test_invalid_existing_file_or_generated_mismatch_prevents_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / voice.LSR
            target.write_bytes(b"IP-CIDR,10.0.0.1/32,no-resolve\n")
            with self.assertRaises(voice.SyncError):
                voice.sync(FIXTURE.read_bytes(), root)
            self.assertEqual(target.read_bytes(), b"IP-CIDR,10.0.0.1/32,no-resolve\n")
            with patch.object(voice, "render_output", return_value=b"IP-CIDR,8.8.8.8/32,no-resolve\n"), \
                    self.assertRaises(voice.SyncError):
                voice.sync(FIXTURE.read_bytes(), root)

    def test_unchanged_or_reordered_source_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            voice.sync(FIXTURE.read_bytes(), root)
            doc = json.loads(FIXTURE.read_bytes())
            doc["prefixes"].reverse()
            with patch.object(voice, "atomic_write") as write:
                self.assertEqual(voice.sync(json.dumps(doc).encode(), root), ([], 23))
            write.assert_not_called()

    def test_download_failure_returns_nonzero_without_writes(self):
        with patch.object(voice, "fetch_source", side_effect=voice.SyncError("HTTP 403")), \
                patch.object(voice, "atomic_write") as write, redirect_stderr(StringIO()):
            self.assertEqual(voice.main([]), 1)
            write.assert_not_called()

    def test_livekit_already_covered_by_existing_ai_rules(self):
        rules = (Path(__file__).parents[1] / "Ai.lsr").read_text()
        self.assertIn("DOMAIN-SUFFIX,livekit.cloud\n", rules)

    def test_no_embedded_policy_or_yaml_is_accepted(self):
        for data in [b"IP-CIDR,8.8.8.8/32,PROXY,no-resolve\n",
                     b"IP-CIDR,8.8.8.8/32,DIRECT\n",
                     b"payload:\n  - '8.8.8.8/32'\n",
                     b"IP-CIDR6,8.8.8.8/32,no-resolve\n"]:
            with self.subTest(data=data), self.assertRaises(voice.SyncError):
                voice.read_rules(data)


if __name__ == "__main__":
    unittest.main()
