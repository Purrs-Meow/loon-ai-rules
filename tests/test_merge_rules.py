from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import merge_rules as merged
from scripts import sync_rules
from scripts.sync_voice import parse_source, render_output

ROOT = Path(__file__).parents[1]


class MergeTests(unittest.TestCase):
    def test_suffix_boundaries_duplicates_and_case_are_conservative(self):
        rules = ["DOMAIN,a.example.com", "DOMAIN-SUFFIX,example.com",
                 "DOMAIN-SUFFIX,sub.example.com", "DOMAIN,example.com",
                 "DOMAIN,notexample.com", "DOMAIN,notexample.com",
                 "DOMAIN,EXAMPLE.com", "DOMAIN-SUFFIX,example.com."]
        self.assertEqual(merged.deduplicate(rules), [rules[1], rules[4], rules[6], rules[7]])

    def test_real_snapshot_union_coverage_and_failure_safety(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = sync_rules.render_output(sync_rules.parse_source(
                (ROOT / "tests/fixtures/Ai.yaml").read_bytes())[0])
            (root / "Ai.lsr").write_bytes(source)
            nets = parse_source((ROOT / "tests/fixtures/chatgpt-voice.json").read_bytes())
            (root / "ChatGPT-Voice.lsr").write_bytes(render_output(nets))
            self.assertEqual(merged.merge(root), (99, 23))
            output = (root / merged.OUTPUT).read_text()
            rules = {r for r in output.splitlines() if r and not r.startswith("#")}
            self.assertEqual(len(rules), 122)
            self.assertIn("DOMAIN-SUFFIX,livekit.cloud", rules)
            self.assertIn("# AUTHOR: https://t.me/ddgksf2021", output)
            for net in nets:
                self.assertIn(f"IP-CIDR,{net},no-resolve", rules)
            for rule in merged.read_existing_rules(source):
                kind, domain = rule.split(",")
                self.assertTrue(rule in rules or any(
                    r.startswith("DOMAIN-SUFFIX,") and
                    (domain.endswith("." + r.split(",")[1]) or
                     (kind == "DOMAIN" and domain == r.split(",")[1])) for r in rules))
            self.assertEqual((root / "Ai.lsr").read_bytes(), source)
            with patch.object(merged, "atomic_write") as write:
                merged.merge(root)
                write.assert_not_called()
            before = (root / merged.OUTPUT).read_bytes()
            for path, bad in [("Ai.lsr", b"DOMAIN,x.com,DIRECT\n"),
                              ("ChatGPT-Voice.lsr", b"<html>error</html>")]:
                old = (root / path).read_bytes()
                (root / path).write_bytes(bad)
                with self.assertRaises(merged.SyncError):
                    merged.merge(root)
                self.assertEqual((root / merged.OUTPUT).read_bytes(), before)
                (root / path).write_bytes(old)
