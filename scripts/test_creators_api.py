#!/usr/bin/env python3
"""One-request smoke test for Amazon Creators API. Does not write queue/cache."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from product_resolver import ProductResolutionError, _search_amazon_creators_api  # noqa: E402


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "wireless headphones"
    os.environ["PRODUCT_DISCOVERY_PROVIDER"] = "creators_api"
    try:
        rows = _search_amazon_creators_api(query)
    except ProductResolutionError as exc:
        print(f"CREATORS_API_TEST_FAILED: {exc}")
        return 1

    if not rows:
        print(f"CREATORS_API_TEST_FAILED: no product results for query: {query}")
        return 1

    print(json.dumps({
        "success": True,
        "provider": "amazon_creators_api",
        "query": query,
        "result_count": len(rows),
        "sample": [{"title": row["title"], "asin": row["asin"]} for row in rows[:3]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
