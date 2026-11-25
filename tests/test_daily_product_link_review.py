#!/usr/bin/env python3
"""Offline tests for the daily external product-link review."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import daily_product_link_review as review  # noqa: E402


class TestDailyProductLinkReview(unittest.TestCase):
    def test_extracts_only_direct_amazon_product_links(self):
        html = (
            '<a href="https://www.amazon.com/dp/B012345678?tag=hotr0e-20">View Amazon</a>'
            '<a href="https://example.com/dp/B099999999">Other</a>'
            '<a href="https://www.amazon.com/gp/product/B087654321?tag=hotr0e-20">Second</a>'
        )
        self.assertEqual(
            review._anchor_asins(html),
            {"B012345678": "View Amazon", "B087654321": "Second"},
        )

    def test_join_feed_to_queue_metadata_and_deduplicate_asins(self):
        posts = [
            {
                "title": "A Product Review",
                "url": "https://www.honesttrove.com/review.html",
                "published": "2026-11-25T00:00:00Z",
                "content": '<a href="https://www.amazon.com/dp/B012345678?tag=hotr0e-20">View on Amazon</a>',
            },
            {
                "title": "Another Review",
                "url": "https://www.honesttrove.com/other.html",
                "published": "2026-11-24T00:00:00Z",
                "content": '<a href="https://www.amazon.com/dp/B012345678?tag=hotr0e-20">View on Amazon</a>',
            },
        ]
        index = {"posts": [{
            "title": "A Product Review",
            "items": [{
                "name": "Desk Lamp",
                "asin": "B012345678",
                "verified_product_title": "Northstar Desk Lamp Model NL200",
                "search_term": "Northstar NL200",
            }],
        }]}
        products = review.build_product_inventory(posts, index)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["product_name"], "Desk Lamp")
        self.assertEqual(products[0]["verified_product_title"], "Northstar Desk Lamp Model NL200")
        self.assertEqual(len(products[0]["posts"]), 2)

    def test_generic_anchor_uses_article_title_when_queue_metadata_is_missing(self):
        posts = [{
            "title": "MOSECYOU Deadbolt Lockout Device Review: What It Blocks",
            "url": "https://www.honesttrove.com/lock.html",
            "published": "2026-11-16T09:43:33Z",
            "content": '<a href="https://www.amazon.com/dp/B004L1W56G?tag=hotr0e-20">View on Amazon</a>',
        }]
        products = review.build_product_inventory(posts, {"posts": []})
        self.assertEqual(len(products), 1)
        self.assertNotEqual(products[0]["product_name"], "View on Amazon")
        self.assertIn("MOSECYOU Deadbolt Lockout Device", products[0]["product_name"])

    def test_third_party_in_stock_is_never_amazon_confirmation(self):
        product = {
            "asin": "B012345678",
            "product_name": "Desk Lamp",
            "verified_product_title": "Northstar Desk Lamp Model NL200",
            "posts": [],
        }
        payload = {"results": [{
            "title": "Northstar NL200 Desk Lamp - In Stock",
            "url": "https://www.desertcart.com/products/northstar-nl200",
            "content": "Northstar Desk Lamp Model NL200. In stock and ready to ship for $79.99.",
        }], "usage": {"credits": 1}}
        result = review.analyse_tavily_results(product, payload)
        self.assertEqual(result["external_summary"], "SINGLE_EXTERNAL_IN_STOCK_SIGNAL")
        self.assertFalse(result["amazon_inventory_verified"])
        self.assertFalse(result["automatic_replacement_allowed"])
        self.assertTrue(result["manual_amazon_confirmation_required"])

    def test_conflicting_retailer_signals_are_not_resolved_as_in_stock(self):
        product = {
            "asin": "B012345678",
            "product_name": "Desk Lamp",
            "verified_product_title": "Northstar Desk Lamp Model NL200",
            "posts": [],
        }
        payload = {"results": [
            {
                "title": "Northstar NL200 Desk Lamp in stock",
                "url": "https://www.desertcart.com/products/nl200",
                "content": "Model NL200 is in stock.",
            },
            {
                "title": "Northstar NL200 Desk Lamp",
                "url": "https://www.ubuy.com/products/nl200",
                "content": "Model NL200 is currently unavailable.",
            },
        ]}
        result = review.analyse_tavily_results(product, payload)
        self.assertEqual(result["external_summary"], "CONFLICTING_EXTERNAL_SIGNALS")
        self.assertFalse(result["amazon_inventory_verified"])

    def test_amazon_results_are_excluded_as_external_evidence(self):
        product = {
            "asin": "B012345678",
            "product_name": "Desk Lamp",
            "verified_product_title": "Northstar Desk Lamp Model NL200",
            "posts": [],
        }
        payload = {"results": [{
            "title": "Northstar Desk Lamp In Stock",
            "url": "https://www.amazon.com/dp/B012345678",
            "content": "In stock.",
        }]}
        result = review.analyse_tavily_results(product, payload)
        self.assertEqual(result["external_summary"], "NO_MATCHING_EXTERNAL_SOURCE")
        self.assertEqual(result["sources"], [])
        self.assertFalse(result["amazon_inventory_verified"])

    def test_daily_batch_rotates_and_respects_budget_cap(self):
        products = [{"asin": f"B00000000{i}"} for i in range(1, 31)]
        batch, next_cursor = review._next_batch(products, 25, 15)
        self.assertEqual(len(batch), 15)
        self.assertEqual(batch[0]["asin"], products[25]["asin"])
        self.assertEqual(next_cursor, 10)
        self.assertEqual(review.HARD_DAILY_QUERY_CAP, 20)

    def test_negative_stock_signal_is_detected(self):
        self.assertEqual(
            review._stock_signal("Currently unavailable; not in stock."),
            "EXTERNAL_OUT_OF_STOCK_SIGNAL",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
