#!/usr/bin/env python3
"""Convert the upstream's strict YAML subset to a Loon remote rule list.

No third-party dependencies. This deliberately is NOT a general YAML parser:
unsupported syntax or rule types fail closed rather than silently losing rules.
"""

from __future__ import annotations

import argparse
from collections import Counter
from http.client import IncompleteRead
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOURCE_URL = "https://ddgksf2013.top/filter/Ai.yaml"
MAX_BYTES = 512 * 1024
MIN_RULES = 50
MIN_RETAINED_RATIO = 0.75
ALLOWED_TYPES = {"DOMAIN", "DOMAIN-SUFFIX"}
RETRY_STATUS = {429, 500, 502, 503, 504}
HEADER = (
    "# FORMAT: Loon remote rule list (.lsr)\n"
    f"# SOURCE: {SOURCE_URL}\n"
    "# Converted from YAML; rule order and matching semantics are preserved.\n"
    "# Select an existing policy in Loon when adding this subscription.\n"
)
LABEL = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?\Z")


class SyncError(Exception):
    """A download, format or safety check failed; keep the existing output."""


class HTTPSOnlyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urlsplit(newurl)
        if parts.scheme != "https" or parts.username or parts.password:
            raise SyncError("Refusing an insecure or credential-bearing redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def decode_source(data: bytes, content_type: str = "") -> str:
    if not data or len(data) > MAX_BYTES:
        raise SyncError("Source is empty or exceeds the 512 KiB size limit")
    if "html" in content_type.lower():
        raise SyncError("Source returned HTML instead of YAML")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SyncError("Source is not valid UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if any((ord(c) < 32 and c != "\n") or 127 <= ord(c) <= 159 or c in "\u2028\u2029" for c in text):
        raise SyncError("Source contains unsupported control characters or tabs")
    if re.search(r"<!doctype\s+html|<html(?:\s|>)", text, re.I):
        raise SyncError("Source contains an HTML error page")
    if not text.strip():
        raise SyncError("Source is empty")
    return text


def fetch_source(url: str = SOURCE_URL, attempts: int = 3) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password:
        raise SyncError("Source URL must use HTTPS without embedded credentials")
    request = Request(url, headers={
        "User-Agent": "loon-ai-rules/1.0 (+https://github.com/Purrs-Meow/loon-ai-rules)",
        "Accept": "text/plain, application/yaml, text/yaml, */*;q=0.1",
    })
    opener = build_opener(HTTPSOnlyRedirect())
    for attempt in range(attempts):
        try:
            with opener.open(request, timeout=25) as response:
                if response.status != 200:
                    raise SyncError(f"Unexpected HTTP status: {response.status}")
                content_type = response.headers.get("Content-Type", "")
                length = response.headers.get("Content-Length")
                if length and (not length.isdigit() or int(length) > MAX_BYTES):
                    raise SyncError("Invalid or oversized Content-Length")
                data = response.read(MAX_BYTES + 1)
                if length and int(length) != len(data):
                    raise SyncError("Response was truncated or Content-Length is inconsistent")
                decode_source(data, content_type)
                return data
        except HTTPError as exc:
            exc.close()
            if exc.code not in RETRY_STATUS or attempt == attempts - 1:
                raise SyncError(f"Source download failed: HTTP {exc.code}") from exc
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as exc:
            if attempt == attempts - 1:
                raise SyncError(f"Source download failed: {exc}") from exc
        if attempt < attempts - 1:
            time.sleep(3 * (attempt + 1))
    raise SyncError("No download attempts were made")


def scalar_value(value: str, line_number: int) -> str:
    """Accept plain, single-quoted, or JSON-compatible double-quoted strings."""
    if value.startswith('"'):
        try:
            scalar, end = json.JSONDecoder().raw_decode(value)
        except json.JSONDecodeError as exc:
            raise SyncError(f"Line {line_number}: unsupported quoted scalar") from exc
        rest = value[end:]
        if not isinstance(scalar, str) or (rest and not re.fullmatch(r" +(?:#.*)?", rest)):
            raise SyncError(f"Line {line_number}: invalid trailing scalar content")
        return scalar
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'(?: +(?:#.*)?)?", value)
        if not match:
            raise SyncError(f"Line {line_number}: unsupported quoted scalar")
        return match.group(1).replace("''", "'")
    return re.split(r" +#", value, maxsplit=1)[0].rstrip(" ")


def validate_rule(rule: str, line_number: int) -> str:
    parts = rule.split(",")
    if len(parts) != 2 or parts[0] not in ALLOWED_TYPES:
        raise SyncError(f"Line {line_number}: unsupported rule type or field count")
    domain = parts[1]
    # Preserve case and a possible root dot; no broad-domain filtering or deduping.
    host = domain[:-1] if domain.endswith(".") else domain
    if not host or len(host) > 253 or not all(LABEL.fullmatch(label) for label in host.split(".")):
        raise SyncError(f"Line {line_number}: invalid domain syntax")
    return rule


def parse_source(data: bytes) -> tuple[list[str], list[str]]:
    """Read one payload map with exactly two-space-indented scalar items."""
    lines: list[str] = []
    rules: list[str] = []
    found_payload = False
    for line_number, line in enumerate(decode_source(data).split("\n"), 1):
        stripped = line.strip(" ")
        if not stripped or stripped.startswith("#"):
            lines.append(stripped)
            continue
        if re.fullmatch(r"payload:(?: +(?:#.*)?)?", line):
            if found_payload:
                raise SyncError(f"Line {line_number}: duplicate payload key")
            found_payload = True
            continue
        if not found_payload or not line.startswith("  - "):
            raise SyncError(f"Line {line_number}: unsupported YAML structure")
        rule = validate_rule(scalar_value(line[4:].rstrip(" "), line_number), line_number)
        rules.append(rule)
        lines.append(rule)
    if not found_payload or not rules:
        raise SyncError("Source must contain one non-empty payload list")
    return lines, rules


def read_existing_rules(data: bytes) -> list[str]:
    rules = [
        validate_rule(line.strip(" "), number)
        for number, line in enumerate(decode_source(data).split("\n"), 1)
        if line.strip(" ") and not line.strip(" ").startswith("#")
    ]
    if not rules:
        raise SyncError("Existing output has no valid rules; review it manually")
    return rules


def validate_counts(new_count: int, previous_count: int | None) -> None:
    if new_count < MIN_RULES:
        raise SyncError(f"Only {new_count} rules; minimum is {MIN_RULES}")
    if previous_count is not None and new_count < previous_count * MIN_RETAINED_RATIO:
        raise SyncError(
            f"Rule count dropped from {previous_count} to {new_count}; "
            "more than 25% loss requires manual review"
        )


def render_output(lines: list[str]) -> bytes:
    body = "\n".join(lines).strip("\n")
    return (HEADER + "\n" + body + "\n").encode("utf-8")


def atomic_write(path: Path, data: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o644)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def sync(data: bytes, output: Path) -> tuple[bool, Counter]:
    lines, rules = parse_source(data)
    existing = output.read_bytes() if output.exists() else None
    previous = len(read_existing_rules(existing)) if existing is not None else None
    validate_counts(len(rules), previous)
    result = render_output(lines)
    # Independently validate the generated text before touching the destination.
    if read_existing_rules(result) != rules:
        raise SyncError("Generated rules do not exactly match the source")
    changed = result != existing
    if changed:
        atomic_write(output, result)
    return changed, Counter(rule.split(",", 1)[0] for rule in rules)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Convert an already downloaded source instead of fetching")
    parser.add_argument("--output", type=Path, default=Path("Ai.lsr"))
    args = parser.parse_args(argv)
    try:
        data = args.input.read_bytes() if args.input else fetch_source()
        changed, counts = sync(data, args.output)
    except (SyncError, OSError, ValueError) as exc:
        print(f"Sync failed; existing output was not replaced: {exc}", file=sys.stderr)
        return 1
    total = sum(counts.values())
    print(f"{'Updated' if changed else 'Unchanged'} {args.output}: {total} rules "
          f"(DOMAIN={counts['DOMAIN']}, DOMAIN-SUFFIX={counts['DOMAIN-SUFFIX']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
