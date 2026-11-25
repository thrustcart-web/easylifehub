#!/usr/bin/env python3
"""Offline tests for SerpApi team-key failover behavior."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import call, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import product_resolver as resolver  # noqa: E402


class TestSerpApiKeyFailover(unittest.TestCase):
    def test_reads_four_named_keys_in_order_and_deduplicates(self):
        env = {
            "SERPAPI_API_KEY": "primary",
            "SERPAPI_API_KEY_2": "team-2",
            "SERPAPI_API_KEY_3": "team-3",
            "SERPAPI_API_KEY_4": "team-2",
        }
        with patch.dict(os.environ, env, clear=False):
            keys = resolver._serpapi_api_keys()
            self.assertEqual(keys, ["primary", "team-2", "team-3"])

    def test_falls_back_after_transient_server_error(self):
        env = {
            "SERPAPI_API_KEY": "primary",
            "SERPAPI_API_KEY_2": "team-2",
            "SERPAPI_API_KEY_3": "",
            "SERPAPI_API_KEY_4": "",
        }
        rows = [{"asin": "B000000001", "title": "Example", "stock": "In Stock"}]
        with patch.dict(os.environ, env, clear=False):
            with patch.object(
                resolver,
                "_search_amazon_serpapi",
                side_effect=[
                    resolver.ProductResolutionError("SerpApi HTTP 503: temporary outage"),
                    rows,
                ],
            ) as mocked:
                result = resolver._search_amazon_with_provider("example", "serpapi", "primary")
        self.assertEqual(result, rows)
        self.assertEqual(
            mocked.call_args_list,
            [call("example", "primary"), call("example", "team-2")],
        )

    def test_does_not_fall_back_after_quota_or_rate_limit_error(self):
        env = {
            "SERPAPI_API_KEY": "primary",
            "SERPAPI_API_KEY_2": "team-2",
            "SERPAPI_API_KEY_3": "",
            "SERPAPI_API_KEY_4": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(
                resolver,
                "_search_amazon_serpapi",
                side_effect=resolver.ProductResolutionError(
                    "SerpApi HTTP 429: Your account has run out of searches"
                ),
            ) as mocked:
                with self.assertRaises(resolver.ProductResolutionError):
                    resolver._search_amazon_with_provider("example", "serpapi", "primary")
        mocked.assert_called_once_with("example", "primary")


if __name__ == "__main__":
    unittest.main(verbosity=2)
