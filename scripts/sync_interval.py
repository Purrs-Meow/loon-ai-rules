#!/usr/bin/env python3
"""Gate upstream downloads on 15 elapsed days since the last successful sync."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

from scripts.sync_rules import atomic_write

INTERVAL = timedelta(days=15)
STATE = Path(".sync-state.json")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Sync timestamp must be a timezone-aware ISO 8601 string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Sync timestamp must include a timezone")
    return result.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def check_due(event: str, state: Path = STATE, now: datetime | None = None) -> tuple[bool, str]:
    if event == "workflow_dispatch":
        return True, "Manual run: force sync and restart the 15-day interval after success."
    if event != "schedule":
        return False, "Code/documentation change: tests only; no upstream download."
    now = utc_now() if now is None else now
    if not state.exists():
        return True, "No successful sync record yet; sync is due."
    record = json.loads(state.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or "last_success_utc" not in record:
        raise ValueError("Invalid sync state; review .sync-state.json or run a manual sync")
    last = timestamp(record["last_success_utc"])
    if last > now:
        raise ValueError("Last successful sync is in the future; review the state or run a manual sync")
    next_due = last + INTERVAL
    if now >= next_due:
        return True, f"15 days have elapsed since {format_time(last)}; sync is due."
    return False, f"Not due: next eligible time is {format_time(next_due)}; no upstream download."


def record_success(state: Path = STATE, now: datetime | None = None) -> None:
    """Stage success locally; the workflow must commit AND push it with Ai.lsr."""
    now = utc_now() if now is None else now
    data = json.dumps({"last_success_utc": format_time(now)}, indent=2) + "\n"
    atomic_write(state, data.encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "record"))
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "push"))
    parser.add_argument("--state", type=Path, default=STATE)
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            record_success(args.state)
            print("Staged successful sync time; it becomes durable only after git push succeeds.")
        else:
            due, message = check_due(args.event, args.state)
            print(message)
            if output := os.environ.get("GITHUB_OUTPUT"):
                with open(output, "a", encoding="utf-8") as handle:
                    handle.write(f"due={str(due).lower()}\n")
            if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write(message + "\n")
    except (OSError, ValueError, OverflowError) as exc:
        print(f"Sync interval check/record failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
