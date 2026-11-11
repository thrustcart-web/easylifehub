#!/usr/bin/env python3
"""Safely replace the configured Amazon Associates tracking tag in queued content."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
QUEUE_DIR = ROOT / "content" / "queue"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/update_affiliate_tag.py NEW_TRACKING_ID", file=sys.stderr)
        return 2

    new_tag = sys.argv[1].strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{3,64}", new_tag):
        print("ERROR: tracking ID must contain only letters, numbers, and hyphens.", file=sys.stderr)
        return 2

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    affiliate = config.setdefault("affiliate", {})
    old_tag = str(affiliate.get("tag") or "").strip()

    if not old_tag:
        print("ERROR: config.json does not contain affiliate.tag; refusing to guess the old tag.", file=sys.stderr)
        return 1

    if old_tag == new_tag:
        print(f"Tracking ID is already {new_tag}; no changes needed.")
        return 0

    pattern = re.compile(
        r"(?P<prefix>[?&]tag=)" + re.escape(old_tag) +
        r"(?P<suffix>(?:&|&amp;|[\\\"'\\s<>]|$))"
    )
    changed_files = 0
    replacements = 0

    if QUEUE_DIR.exists():
        for path in sorted(QUEUE_DIR.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".html", ".json", ".md"}:
                continue
            original = path.read_text(encoding="utf-8")
            updated, count = pattern.subn(
                lambda match: match.group("prefix") + new_tag + match.group("suffix"),
                original,
            )
            if count:
                path.write_text(updated, encoding="utf-8")
                changed_files += 1
                replacements += count
                print(f"UPDATED {path.relative_to(ROOT)}: {count} link(s)")

    affiliate["tag"] = new_tag
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"CONFIG updated: {old_tag} -> {new_tag}")
    print(f"Queue files changed: {changed_files}")
    print(f"Affiliate links retagged: {replacements}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
