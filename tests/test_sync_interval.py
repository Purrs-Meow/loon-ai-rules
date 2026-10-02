"""Offline interval and real-git persistence tests; never contact upstream."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from scripts import sync_interval as interval
from scripts import sync_rules

ROOT = Path(__file__).resolve().parents[1]


class IntervalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / ".sync-state.json"
        self.last = interval.timestamp("2026-10-02T09:40:00Z")
        interval.record_success(self.state, self.last)

    def test_before_exact_and_after_15_day_boundary(self):
        for delta, expected in [(timedelta(days=15, seconds=-1), False),
                                (timedelta(days=15), True),
                                (timedelta(days=16), True)]:
            with self.subTest(delta=delta):
                self.assertEqual(interval.check_due("schedule", self.state, self.last + delta)[0], expected)

    def test_month_year_and_leap_year_boundaries(self):
        for start, due in [("2026-01-31T21:37:00Z", "2026-02-15T21:37:00Z"),
                           ("2026-12-25T21:37:00Z", "2027-01-09T21:37:00Z"),
                           ("2028-02-20T21:37:00Z", "2028-03-06T21:37:00Z")]:
            with self.subTest(start=start):
                interval.record_success(self.state, interval.timestamp(start))
                self.assertFalse(interval.check_due("schedule", self.state, interval.timestamp(due) - timedelta(seconds=1))[0])
                self.assertTrue(interval.check_due("schedule", self.state, interval.timestamp(due))[0])

    def test_offset_is_normalized_to_utc(self):
        self.assertEqual(interval.timestamp("2026-10-02T17:40:00+08:00"), self.last)

    def test_manual_always_forces_even_corrupt_or_future_state(self):
        for value in ["broken", '{"last_success_utc":"2999-01-01T00:00:00Z"}']:
            self.state.write_text(value)
            self.assertTrue(interval.check_due("workflow_dispatch", self.state, self.last)[0])

    def test_push_only_tests_even_with_missing_or_overdue_state(self):
        self.assertFalse(interval.check_due("push", self.state, self.last + timedelta(days=100))[0])
        self.state.unlink()
        self.assertFalse(interval.check_due("push", self.state, self.last)[0])

    def test_missing_state_is_due(self):
        self.state.unlink()
        self.assertTrue(interval.check_due("schedule", self.state, self.last)[0])

    def test_bad_state_fails_closed(self):
        for value in ["broken", "[]", "{}", '{"last_success_utc":null}',
                      '{"last_success_utc":"2026-10-02T09:40:00"}',
                      '{"last_success_utc":"2999-01-01T00:00:00Z"}']:
            with self.subTest(value=value):
                self.state.write_text(value)
                with self.assertRaises(ValueError):
                    interval.check_due("schedule", self.state, self.last)

    def test_gate_never_rewrites_state(self):
        before = self.state.read_bytes()
        for event in ("push", "schedule", "workflow_dispatch"):
            interval.check_due(event, self.state, self.last)
        self.assertEqual(self.state.read_bytes(), before)

    def test_success_resets_next_due_time(self):
        later = self.last + timedelta(days=5)
        interval.record_success(self.state, later)
        self.assertFalse(interval.check_due("schedule", self.state, self.last + timedelta(days=15))[0])
        self.assertTrue(interval.check_due("schedule", self.state, later + timedelta(days=15))[0])

    def test_atomic_state_write_failure_preserves_old_time(self):
        before = self.state.read_bytes()
        with patch.object(sync_rules.os, "replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            interval.record_success(self.state, self.last + timedelta(days=15))
        self.assertEqual(self.state.read_bytes(), before)

    def test_cli_emits_skip_without_fetch_or_state_update(self):
        output = self.state.with_name("output")
        summary = self.state.with_name("summary")
        before = self.state.read_bytes()
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}), patch.object(interval, "utc_now", return_value=self.last), patch.object(sync_rules, "fetch_source") as fetch, redirect_stdout(StringIO()):
            self.assertEqual(interval.main(["check", "--event", "schedule", "--state", str(self.state)]), 0)
        self.assertEqual(output.read_text(), "due=false\n")
        self.assertIn("2026-10-17T09:40:00Z", summary.read_text())
        self.assertEqual(self.state.read_bytes(), before)
        fetch.assert_not_called()

    def test_cli_reports_invalid_state_without_due_output(self):
        self.state.write_text("broken")
        output = self.state.with_name("output")
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), redirect_stderr(StringIO()):
            self.assertEqual(interval.main(["check", "--event", "schedule", "--state", str(self.state)]), 1)
        self.assertFalse(output.exists())


class WorkflowPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.repo = self.base / "checkout"
        self.remote = self.base / "remote.git"
        self.repo.mkdir()
        self.git("init", "--bare", str(self.remote), cwd=self.base)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("remote", "add", "origin", str(self.remote))
        self.data = (ROOT / "tests/fixtures/Ai.yaml").read_bytes()
        sync_rules.sync(self.data, self.repo / "Ai.lsr")
        interval.record_success(self.repo / ".sync-state.json", interval.timestamp("2020-01-01T00:00:00Z"))
        self.git("add", ".")
        self.git("commit", "-m", "initial fixture")
        self.git("push", "-u", "origin", "main")
        self.original_remote = self.remote_state()
        workflow = (ROOT / ".github/workflows/sync.yml").read_text()
        self.persist = textwrap.dedent(workflow.split("        run: |\n", 1)[1])
        self.env = {**os.environ, "PYTHONPATH": str(ROOT), "GITHUB_STEP_SUMMARY": str(self.base / "summary")}

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, check=True, capture_output=True, text=True).stdout

    def remote_state(self):
        return self.git("--git-dir", str(self.remote), "show", "refs/heads/main:.sync-state.json")

    def run_persist(self):
        return subprocess.run(["bash", "-c", self.persist], cwd=self.repo, env=self.env, capture_output=True, text=True)

    def test_unchanged_source_still_persists_success(self):
        before_rules = (self.repo / "Ai.lsr").read_bytes()
        self.assertFalse(sync_rules.sync(self.data, self.repo / "Ai.lsr")[0])
        result = self.run_persist()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.remote_state(), self.original_remote)
        self.assertEqual((self.repo / "Ai.lsr").read_bytes(), before_rules)
        self.assertEqual(self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").strip(), ".sync-state.json")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.git("--git-dir", str(self.remote), "rev-parse", "refs/heads/main").strip())

    def test_failed_push_does_not_advance_remote_success(self):
        self.git("remote", "set-url", "origin", str(self.base / "missing.git"))
        result = self.run_persist()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.remote_state(), self.original_remote)

    def test_download_or_validation_failure_does_not_persist(self):
        for data, error in [(None, sync_rules.SyncError("HTTP 403")), (b"<html>bad</html>", None)]:
            with self.subTest(error=error), patch.object(sync_rules, "fetch_source", return_value=data, side_effect=error), redirect_stderr(StringIO()):
                result = sync_rules.main(["--output", str(self.repo / "Ai.lsr")])
                if result == 0:
                    self.run_persist()
                self.assertEqual(result, 1)
                self.assertEqual(self.remote_state(), self.original_remote)
                self.assertEqual((self.repo / ".sync-state.json").read_text(), self.original_remote)

    def test_workflow_gates_download_and_persistence_after_success(self):
        workflow = (ROOT / ".github/workflows/sync.yml").read_text()
        self.assertEqual(workflow.count("if: steps.interval.outputs.due == 'true'"), 2)
        self.assertNotIn("always()", workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertLess(workflow.index("python3 scripts/sync_rules.py"), workflow.index("python3 -m scripts.sync_interval record"))


if __name__ == "__main__":
    unittest.main()
