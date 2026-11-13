#!/usr/bin/env python3
"""Read-only smoke test for Amazon Creators SearchItems and GetItems."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from product_resolver import (  # noqa: E402
    ProductResolutionError,
    STOCK_ALLOWED,
    _creators_get_items_by_asin,
    _search_amazon_creators_api,
)


def main() -> int:
    query = " ".join(sys.argv[1:]).strip() or "wireless headphones"
    os.environ["PRODUCT_DISCOVERY_PROVIDER"] = "creators_api"
    try:
        search_rows = _search_amazon_creators_api(query)
        if not search_rows:
            print(f"CREATORS_API_TEST_FAILED: no SearchItems results for query: {query}")
            return 1

        # Verify up to ten fresh SearchItems results in one GetItems transaction.
        requested_asins = [row["asin"] for row in search_rows[:10]]
        get_items_rows = _creators_get_items_by_asin(requested_asins)
    except ProductResolutionError as exc:
        print(f"CREATORS_API_TEST_FAILED: {exc}")
        return 1

    verified_in_stock = [
        row for row in get_items_rows.values()
        if row.get("availability_type") in STOCK_ALLOWED
    ]
    report = {
        "success": bool(verified_in_stock),
        "provider": "amazon_creators_api",
        "query": query,
        "search_result_count": len(search_rows),
        "get_items_requested": len(requested_asins),
        "get_items_returned": len(get_items_rows),
        "get_items_in_stock": len(verified_in_stock),
        "get_items_sample": [
            {
                "title": row["title"],
                "asin": row["asin"],
                "parent_asin": row.get("parent_asin", row["asin"]),
                "availability_type": row.get("availability_type", "UNKNOWN"),
            }
            for row in list(get_items_rows.values())[:3]
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not verified_in_stock:
        print(
            "CREATORS_API_TEST_FAILED: SearchItems worked, but GetItems returned "
            "no explicitly in-stock products. Check response/access/rate-limit details."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
