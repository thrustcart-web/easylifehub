#!/usr/bin/env python3
"""One-time maintenance command: replace Amazon search links with verified product URLs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from product_resolver import backfill_queue  # noqa: E402


if __name__ == "__main__":
    summary = backfill_queue()
    print(json.dumps(summary, indent=2))
