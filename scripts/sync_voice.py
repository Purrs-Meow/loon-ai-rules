#!/usr/bin/env python3
"""Generate Loon ChatGPT Voice IP rules from OpenAI's official JSON."""
from __future__ import annotations

import argparse
from datetime import datetime
import ipaddress
import json
from pathlib import Path
import sys

from scripts.sync_rules import SyncError, atomic_write, decode_source, fetch_source

SOURCE_URL = "https://openai.com/chatgpt-voice.json"
LSR = "ChatGPT-Voice.lsr"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SyncError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def network(value, version=None):
    if not isinstance(value, str) or "/" not in value:
        raise SyncError("Expected an explicit IP prefix")
    try:
        result = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise SyncError("Invalid or non-canonical IP prefix") from exc
    if version is not None and result.version != version:
        raise SyncError("IP prefix does not match its JSON address family")
    minimum = 16 if result.version == 4 else 32
    if (result.prefixlen < minimum or not result.is_global
            or not result.network_address.is_global
            or not result.broadcast_address.is_global or result.is_multicast):
        raise SyncError("Non-public or unexpectedly broad IP prefix; review upstream")
    return result


def parse_source(data):
    try:
        doc = json.loads(decode_source(data), object_pairs_hook=unique_object)
    except (ValueError, RecursionError) as exc:
        raise SyncError("Invalid voice JSON") from exc
    if not isinstance(doc, dict) or set(doc) != {"creationTime", "prefixes"}:
        raise SyncError("Unexpected voice JSON schema")
    stamp = doc["creationTime"]
    try:
        if not isinstance(stamp, str):
            raise ValueError("not a string")
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("missing timezone")
    except ValueError as exc:
        raise SyncError("Invalid source creationTime") from exc
    entries = doc["prefixes"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1024:
        raise SyncError("Expected 1..1024 voice prefixes")
    networks = set()
    for entry in entries:
        if not isinstance(entry, dict) or len(entry) != 1:
            raise SyncError("Unexpected prefix entry")
        key = next(iter(entry))
        if key not in {"ipv4Prefix", "ipv6Prefix"}:
            raise SyncError("Unknown IP prefix type")
        item = network(entry[key], 4 if key == "ipv4Prefix" else 6)
        if item in networks:
            raise SyncError("Duplicate voice IP prefix")
        networks.add(item)
    return sorted(networks, key=lambda n: (n.version, int(n.network_address), n.prefixlen))


def render_output(networks):
    header = (
        "# FORMAT: Loon remote rule list (.lsr)\n"
        "# ChatGPT Voice IP supplement; keep your OpenAI/LiveKit domain rules.\n"
        f"# SOURCE: {SOURCE_URL}\n"
        "# Select your existing AI proxy/chain policy in Loon.\n"
        "# Match these destination IPs on all ports, including UDP 3478 / TCP 443.\n"
    )
    return (header + "".join(
        f"{'IP-CIDR' if n.version == 4 else 'IP-CIDR6'},{n},no-resolve\n"
        for n in networks
    )).encode("utf-8")


def read_rules(data):
    result = []
    for line in decode_source(data).splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) != 3 or parts[2] != "no-resolve" or parts[0] not in {"IP-CIDR", "IP-CIDR6"}:
            raise SyncError("Invalid Loon voice rule; expected type,prefix,no-resolve")
        result.append(network(parts[1], 4 if parts[0] == "IP-CIDR" else 6))
    if not result or len(result) != len(set(result)):
        raise SyncError("Empty or duplicate voice rules")
    return result


def sync(data, directory):
    networks = parse_source(data)
    content = render_output(networks)
    if read_rules(content) != networks:
        raise SyncError("Generated Loon rules differ from the official prefixes")
    path = directory / LSR
    existing = path.read_bytes() if path.exists() else None
    if existing is not None and len(networks) < len(read_rules(existing)) * 0.75:
        raise SyncError("Voice prefix count dropped by more than 25%; review upstream")
    changed = content != existing
    if changed:
        atomic_write(path, content)
    return [LSR] if changed else [], len(networks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Use a saved official JSON for offline checks")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        data = args.input.read_bytes() if args.input else fetch_source(SOURCE_URL)
        changed, count = sync(data, args.output_dir)
    except (SyncError, OSError, ValueError) as exc:
        print(f"Voice sync failed; do not publish outputs: {exc}", file=sys.stderr)
        return 1
    print(f"Voice IPs: {count}; updated: {', '.join(changed) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
