#!/usr/bin/env python3
"""Offline tests for Tavily's bounded article-research integration."""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ai_writer  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class TestTavilyArticleResearch(unittest.TestCase):
    def setUp(self):
        ai_writer._TAVILY_CALLS_THIS_RUN = 0

    def tearDown(self):
        ai_writer._TAVILY_CALLS_THIS_RUN = 0

    def sample_topic(self):
        return {
            "slug": "northstar-desk-lamp",
            "title": "Northstar Desk Lamp: What to Check Before Buying",
            "keyword": "Northstar Desk Lamp",
            "template": "review",
            "niche": "home office",
            "items": [{
                "name": "Desk Lamp",
                "display_name": "Northstar Desk Lamp",
                "verified_product_title": "Northstar Desk Lamp Model NL200",
                "asin": "B012345678",
            }],
        }

    def test_attaches_exact_model_research_and_strips_stock_and_price_sentences(self):
        payload = {"results": [
            {
                "title": "Northstar NL200 Desk Lamp Manual",
                "url": "https://northstar.example/support/nl200",
                "content": (
                    "The NL200 desk lamp supports three adjustable brightness modes. "
                    "It is in stock today for $99.99."
                ),
            },
            {
                "title": "Northstar NL200 at Amazon",
                "url": "https://www.amazon.com/dp/B012345678",
                "content": "In stock.",
            },
            {
                "title": "Northstar NL200 at Ubuy",
                "url": "https://www.ubuy.com/northstar-nl200",
                "content": "The lamp includes a clamp. In stock.",
            },
        ]}
        topic = self.sample_topic()
        with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key", "TAVILY_MAX_RESEARCH_CALLS_PER_RUN": "3"}):
            with patch.object(ai_writer.request, "urlopen", return_value=FakeResponse(payload)):
                count = ai_writer.attach_tavily_research(topic)

        self.assertEqual(count, 1)
        notes = topic["tavily_research_notes"]
        self.assertEqual(notes[0]["source_domain"], "northstar.example")
        self.assertIn("three adjustable brightness modes", notes[0]["facts"])
        self.assertNotIn("in stock", notes[0]["facts"].lower())
        self.assertNotIn("$99.99", notes[0]["facts"])
        self.assertEqual(ai_writer._TAVILY_CALLS_THIS_RUN, 1)

    def test_missing_key_skips_research_without_network(self):
        topic = self.sample_topic()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(ai_writer.request, "urlopen") as urlopen:
                self.assertEqual(ai_writer.attach_tavily_research(topic), 0)
                urlopen.assert_not_called()
        self.assertNotIn("tavily_research_notes", topic)

    def test_per_run_research_cap_is_enforced(self):
        topic = self.sample_topic()
        ai_writer._TAVILY_CALLS_THIS_RUN = ai_writer.TAVILY_HARD_CALLS_PER_RUN
        with patch.dict(os.environ, {"TAVILY_API_KEY": "fake-key"}):
            with patch.object(ai_writer.request, "urlopen") as urlopen:
                self.assertEqual(ai_writer.attach_tavily_research(topic), 0)
                urlopen.assert_not_called()

    def test_prompt_uses_research_as_context_but_warns_it_is_not_stock_proof(self):
        topic = self.sample_topic()
        topic["tavily_research_notes"] = [{
            "matched_product": "Northstar Desk Lamp Model NL200",
            "source_domain": "northstar.example",
            "facts": "The NL200 desk lamp supports three adjustable brightness modes.",
        }]
        _, user = ai_writer.build_prompts(topic, {
            "content": {"language": "en", "min_words": 501, "target_words": 700},
        })
        self.assertIn("three adjustable brightness modes", user)
        self.assertIn("never proof of Amazon availability", user)
        self.assertIn("Do not mention source URLs, prices, stock status", user)


if __name__ == "__main__":
    unittest.main(verbosity=2)
