"""Build a single Loon subscription from validated AI domains and voice IPs."""
from pathlib import Path
import sys
from scripts.sync_rules import SyncError, atomic_write, decode_source, read_existing_rules
from scripts.sync_voice import read_rules

OUTPUT = "Ai-Combined.lsr"


def deduplicate(rules):
    # All entries use the same policy selected by the subscriber in Loon.
    unique = list(dict.fromkeys(rules))
    suffixes = {r.split(",")[1] for r in unique if r.startswith("DOMAIN-SUFFIX,")}
    return [r for r in unique if not any(
        (r.split(",")[1] == s and r.startswith("DOMAIN,"))
        or r.split(",")[1].endswith("." + s) for s in suffixes)]


def merge(directory):
    domain_data = (directory / "Ai.lsr").read_bytes()
    domains = read_existing_rules(domain_data)
    kept = deduplicate(domains)
    voice_data = (directory / "ChatGPT-Voice.lsr").read_bytes()
    networks = read_rules(voice_data)
    remaining = set(kept)
    lines = []
    # Preserve attribution and comments; remove only redundant rule lines.
    for line in decode_source(domain_data).splitlines():
        rule = line.strip(" ")
        if not rule or rule.startswith("#"):
            if "rule order and matching semantics are preserved" not in rule:
                lines.append(line)
        elif rule in remaining:
            lines.append(rule)
            remaining.remove(rule)
    if remaining:
        raise SyncError("A retained domain rule was lost during merging")
    header = ("# Combined Loon AI + ChatGPT Voice rules; select ONE AI policy.\n"
              "# Sources: Ai.lsr and ChatGPT-Voice.lsr in this repository.\n"
              "# Redundant domain rules removed without expanding coverage.\n")
    data = (header + "\n".join(lines).rstrip() + "\n\n"
            + decode_source(voice_data)).encode("utf-8")
    path = directory / OUTPUT
    if not path.exists() or path.read_bytes() != data:
        atomic_write(path, data)
    return len(kept), len(networks)


def main():
    try:
        domains, ips = merge(Path("."))
    except (SyncError, OSError, ValueError) as exc:
        print(f"Merge failed; do not publish: {exc}", file=sys.stderr)
        return 1
    print(f"Combined Loon rules: {domains} domains + {ips} voice prefixes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
