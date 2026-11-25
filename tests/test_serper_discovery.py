#!/usr/bin/env python3
"""Offline tests for Serper discovery with mandatory Amazon stock verification."""
from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import product_resolver as resolver  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TestSerperDiscovery(unittest.TestCase):
    def test_serper_only_extracts_direct_amazon_product_asins_and_does_not_claim_stock(self):
        payload = {
            "organic": [
                {
                    "title": "Desk Organizer with Drawer",
                    "link": "https://www.amazon.com/Desk-Organizer/dp/B0BYMRJD7R/ref=sr_1_1",
                },
                {
                    "title": "Other retailer result",
                    "link": "https://example.com/products/desk-organizer",
                },
                {
                    "title": "Amazon search results page",
                    "link": "https://www.amazon.com/s?k=desk+organizer",
                },
                {
                    "title": "Second Amazon item",
                    "link": "https://www.amazon.com/gp/product/B0BYDGF8DT",
                },
            ]
        }
        captured = {}
        old_urlopen = resolver.request.urlopen

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            captured["api_key"] = req.get_header("X-api-key")
            return FakeResponse(payload)

        resolver.request.urlopen = fake_urlopen
        old_count = resolver._SERPER_SEARCHES_USED
        resolver._SERPER_SEARCHES_USED = 0
        try:
            with patch.dict(os.environ, {"SERPER_MAX_SEARCHES_PER_RUN": "6"}, clear=False):
                rows = resolver._search_amazon_serper_candidates("desk organizer", "test-serper-key")
        finally:
            resolver.request.urlopen = old_urlopen
            resolver._SERPER_SEARCHES_USED = old_count

        self.assertEqual([row["asin"] for row in rows], ["B0BYMRJD7R", "B0BYDGF8DT"])
        self.assertTrue(all(row["availability_type"] == "UNKNOWN" for row in rows))
        self.assertTrue(all(row["stock_source"] == "serper_candidate" for row in rows))
        self.assertIn("google.serper.dev/search", captured["url"])
        self.assertEqual(captured["payload"]["num"], 10)
        self.assertIn("site:amazon.com", captured["payload"]["q"])
        self.assertEqual(captured["api_key"], "test-serper-key")

    def test_serper_candidate_is_accepted_only_after_apify_confirms_stock(self):
        candidate = {
            "title": "Desk Organizer with Drawer",
            "asin": "B0BYMRJD7R",
            "parent_asin": "B0BYMRJD7R",
            "availability_type": "UNKNOWN",
            "stock_checked_at": 0,
            "stock_source": "serper_candidate",
            "url": "https://www.amazon.com/dp/B0BYMRJD7R",
        }
        verified = {
            "B0BYMRJD7R": {
                "title": "Desk Organizer with Drawer",
                "asin": "B0BYMRJD7R",
                "parent_asin": "B0BYMRJD7R",
                "availability_type": "IN_STOCK",
                "stock_checked_at": int(resolver.time.time()),
                "stock_source": "apify",
                "url": "https://www.amazon.com/dp/B0BYMRJD7R",
            }
        }
        with patch.dict(os.environ, {
            "SERPER_API_KEY": "test-serper-key",
            "TEST_API": "test-apify-token",
            "APIFY_SPEND_ENABLED": "true",
        }, clear=False):
            with patch.object(resolver, "_search_amazon_serper_candidates", return_value=[candidate]):
                with patch.object(resolver, "_apify_get_items_by_asin", return_value=verified) as audit:
                    rows = resolver._search_amazon_serper("desk organizer", "test-serper-key")

        audit.assert_called_once_with(["B0BYMRJD7R"], "test-apify-token")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_source"], "apify")
        self.assertEqual(rows[0]["availability_type"], "IN_STOCK")
        self.assertEqual(rows[0]["discovery_source"], "serper")

    def test_serper_cannot_bypass_apify_stock_guard(self):
        candidate = {
            "title": "Desk Organizer with Drawer",
            "asin": "B0BYMRJD7R",
            "parent_asin": "B0BYMRJD7R",
            "availability_type": "UNKNOWN",
            "stock_checked_at": 0,
            "stock_source": "serper_candidate",
            "url": "https://www.amazon.com/dp/B0BYMRJD7R",
        }
        with patch.dict(os.environ, {
            "SERPER_API_KEY": "test-serper-key",
            "TEST_API": "test-apify-token",
            "APIFY_SPEND_ENABLED": "false",
        }, clear=False):
            with patch.object(resolver, "_search_amazon_serper_candidates", return_value=[candidate]):
                with self.assertRaisesRegex(resolver.ProductResolutionError, "Apify stock-verification guard"):
                    resolver._search_amazon_serper("desk organizer", "test-serper-key")

    def test_serper_run_cap_is_enforced(self):
        old_count = resolver._SERPER_SEARCHES_USED
        resolver._SERPER_SEARCHES_USED = 1
        try:
            with patch.dict(os.environ, {"SERPER_MAX_SEARCHES_PER_RUN": "1"}, clear=False):
                with self.assertRaisesRegex(resolver.ProductResolutionError, "run cap reached"):
                    resolver._search_amazon_serper_candidates("desk organizer", "test-key")
        finally:
            resolver._SERPER_SEARCHES_USED = old_count


if __name__ == "__main__":
    unittest.main(verbosity=2)
