#!/usr/bin/env python3
"""End-to-end tests for the whole pipeline - no network, no API keys required.

    python3 tests/test_pipeline.py

Covers: keyword parsing, topic planning, both writers, rendering, the quality
gate, affiliate links, the provider fallback chain and the circuit breaker.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# keep tests away from the real state/queue
_TMP = Path(tempfile.mkdtemp(prefix="autoblog-test-"))
os.environ.setdefault("AFFILIATE_TAG", "testtag-20")

import affiliate as affiliate_mod            # noqa: E402
import keywords as kw                        # noqa: E402
import providers                             # noqa: E402
import render as render_mod                  # noqa: E402
import safety                                # noqa: E402
import template_engine                       # noqa: E402
import topic_planner                         # noqa: E402
import util                                  # noqa: E402
import ai_writer                            # noqa: E402
import product_resolver                    # noqa: E402
from ai_writer import normalise, validate    # noqa: E402

util.QUEUE_DIR = _TMP / "queue"
util.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
util.STATE_PATH = _TMP / "state.json"
render_mod.QUEUE_DIR = util.QUEUE_DIR


def fresh_cfg() -> dict:
    cfg = util.load_config()
    cfg["affiliate"]["tag"] = "testtag-20"
    cfg["images"]["provider"] = "svg"
    cfg["images"]["access_key"] = ""
    return cfg


class TestKeywordParsing(unittest.TestCase):
    def test_base_extraction_strips_intent(self):
        cases = {
            "best yoga mat for beginners": "yoga mat",
            "dog crate alternatives that cost less": "dog crate",
            "how to choose a standing desk": "standing desk",
            "air fryer buying guide 2026": "air fryer",
            "is a robot vacuum worth it for pet owners": "robot vacuum",
        }
        for keyword, expected in cases.items():
            self.assertEqual(kw.extract_base(keyword), expected, keyword)

    def test_budget_and_audience(self):
        parsed = kw.parse("best slow feeder bowl under 100 dollars for cats", "pets")
        self.assertEqual(parsed["budget"], "under $100")
        self.assertIn("cats", parsed["audience"])
        self.assertEqual(parsed["intent"], "bestof")

    def test_head_noun_groups_families(self):
        self.assertEqual(kw.head_noun("robot vacuum"), kw.head_noun("stick vacuum"))
        self.assertNotEqual(kw.head_noun("robot vacuum"), kw.head_noun("espresso machine"))

    def test_acronyms_keep_capitalisation(self):
        self.assertEqual(kw.title_base("obd2 scanner"), "OBD2 Scanner")
        self.assertEqual(kw.title_base("sd card reader"), "SD Card Reader")
        self.assertEqual(kw.title_base("usb c hub"), "USB C Hub")


class TestArticleDetection(unittest.TestCase):
    def setUp(self):
        self.article = topic_planner._article

    def test_branded_names_get_no_article(self):
        for name in ["Anker 737 Power Bank 24K", "Ninja Creami NC301", "LEVOIT Air Purifier Core 300", "Instant Pot"]:
            self.assertEqual(self.article(name), "", name)

    def test_acronyms_use_vowel_sound(self):
        self.assertEqual(self.article("OBD2 Scanner"), "An")
        self.assertEqual(self.article("SD Card Reader"), "An")

    def test_plurals_and_mass_nouns_get_no_article(self):
        for name in ["Adjustable Dumbbells", "Garden Gloves", "Carry on Luggage", "Resistance Bands Set", "Camping Gear"]:
            self.assertEqual(self.article(name), "", name)

    def test_normal_singulars(self):
        self.assertEqual(self.article("Yoga Mat"), "A")
        self.assertEqual(self.article("Espresso Machine"), "An")
        self.assertEqual(self.article("Stud Finder"), "A")


class TestTopicPlanner(unittest.TestCase):
    def test_topics_are_valid_and_unique(self):
        topics = topic_planner.next_topics(state=util.load_state(), count=25)
        self.assertEqual(len(topics), 25)
        slugs = [t["slug"] for t in topics]
        self.assertEqual(len(set(slugs)), len(slugs), "duplicate slugs in one batch")
        for topic in topics:
            self.assertIn(topic["template"], topic_planner.TEMPLATES)
            self.assertTrue(topic["title"], "empty title")
            self.assertTrue(topic["items"], "no items")
            for item in topic["items"]:
                self.assertTrue(item["display_name"])
                self.assertTrue(item["search_term"])

    def test_template_mix_is_balanced(self):
        topics = topic_planner.next_topics(state=util.load_state(), count=30)
        counts = {t: sum(1 for x in topics if x["template"] == t) for t in topic_planner.TEMPLATES}
        self.assertGreaterEqual(counts["review"], 8)
        self.assertGreaterEqual(counts["bestof"], 5, f"bestof starved: {counts}")

    def test_comparisons_are_actually_comparable(self):
        topics = topic_planner.next_topics(state=util.load_state(), count=60)
        comparisons = [t for t in topics if t["template"] == "comparison"]
        self.assertTrue(comparisons, "no comparisons generated")
        for topic in comparisons:
            a, b = topic["items"][0]["name"], topic["items"][1]["name"]
            shared = kw.tokens_of(a) & kw.tokens_of(b)
            self.assertTrue(
                shared or kw.head_noun(a) == kw.head_noun(b),
                f"nonsensical comparison: {a} vs {b}",
            )

    def test_bestof_lists_have_enough_items(self):
        topics = topic_planner.next_topics(state=util.load_state(), count=60)
        for topic in [t for t in topics if t["template"] == "bestof"]:
            self.assertGreaterEqual(len(topic["items"]), 3)

    def test_deduplication_across_runs(self):
        state = util.load_state()
        first = topic_planner.next_topics(state=state, count=10)
        for topic in first:
            topic_planner.mark_used(state, topic)
            state["generated"].append({"slug": topic["slug"], "template": topic["template"],
                                       "niche": topic["niche"], "family": topic.get("family")})
        second = topic_planner.next_topics(state=state, count=10)
        overlap = {t["slug"] for t in first} & {t["slug"] for t in second}
        self.assertFalse(overlap, f"planner repeated topics: {overlap}")



class TestProductResolution(unittest.TestCase):
    def test_asin_can_be_extracted_from_direct_amazon_url(self):
        asin = product_resolver._extract_asin({
            "url": "https://www.amazon.com/dp/B0BYMRJD7R?language=en"
        })
        self.assertEqual(asin, "B0BYMRJD7R")

    def test_title_matching_requires_all_product_tokens(self):
        self.assertTrue(product_resolver.title_matches(
            "Marbrasse Pen Organizer",
            "Marbrasse Pen Organizer with 2 Drawer for Desk"
        ))
        self.assertFalse(product_resolver.title_matches(
            "Marbrasse Pen Organizer",
            "OPNICE Four Tier Desk File Organizer"
        ))

    def test_resolves_only_matching_asin_from_apify_results(self):
        original_search = product_resolver._search_amazon
        original_save = product_resolver._save_cache
        calls = []

        def fake_search(term, token):
            calls.append((term, token))
            return [
                {"title": "OPNICE Four Tier Desk File Organizer", "asin": "B0BYDGF8DT"},
                {"title": "Marbrasse Pen Organizer with 2 Drawer for Desk", "asin": "B0BYMRJD7R"},
            ]

        product_resolver._search_amazon = fake_search
        product_resolver._save_cache = lambda cache: None
        try:
            items = [{
                "display_name": "Marbrasse Pen Organizer",
                "name": "Marbrasse Pen Organizer",
                "search_term": "desk organizer",
            }]
            result = product_resolver.resolve_items(items, api_token="test-token", cache={})
            self.assertEqual(result[0]["asin"], "B0BYMRJD7R")
            self.assertTrue(result[0]["asin_verified"])
            self.assertEqual(calls, [("desk organizer", "test-token")])
        finally:
            product_resolver._search_amazon = original_search
            product_resolver._save_cache = original_save

    def test_missing_apify_token_blocks_unverified_products(self):
        with self.assertRaisesRegex(product_resolver.ProductResolutionError, "TEST_API"):
            product_resolver.resolve_items(
                [{"display_name": "A Product", "name": "A Product"}],
                api_token="",
                cache={},
            )

    def test_spend_is_disabled_without_explicit_opt_in(self):
        old = os.environ.get("APIFY_SPEND_ENABLED")
        os.environ.pop("APIFY_SPEND_ENABLED", None)
        try:
            with self.assertRaisesRegex(product_resolver.ProductResolutionError, "spending is disabled"):
                product_resolver.resolve_items(
                    [{"display_name": "A Product", "name": "A Product"}],
                    api_token="fake-token",
                    cache={},
                )
        finally:
            if old is not None:
                os.environ["APIFY_SPEND_ENABLED"] = old

    def test_apify_search_omits_delivery_location_and_caps_cost(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'[{"title":"Desk Organizer","asin":"B0BYDGF8DT"}]'

        captured = {}
        original_urlopen = product_resolver.request.urlopen

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        product_resolver.request.urlopen = fake_urlopen
        try:
            rows = product_resolver._search_amazon("desk organizer", "fake-token")
            self.assertEqual(rows[0]["asin"], "B0BYDGF8DT")
        finally:
            product_resolver.request.urlopen = original_urlopen

        self.assertIn("maxTotalChargeUsd=0.015", captured["url"])
        self.assertNotIn("countryCode", captured["payload"])
        self.assertNotIn("zipCode", captured["payload"])
        self.assertEqual(captured["payload"]["maxItemsPerStartUrl"], 3)
        self.assertFalse(captured["payload"]["scrapeProductDetails"])

    def test_apify_account_limit_error_stops_remaining_product_searches(self):
        original_search = product_resolver._search_amazon
        calls = []

        def blocked_search(term, token):
            calls.append(term)
            raise product_resolver.ProductResolutionError(
                "Apify Actor HTTP 403: actor-disabled monthly usage limit"
            )

        product_resolver._search_amazon = blocked_search
        try:
            with self.assertRaisesRegex(product_resolver.ProductResolutionError, "403"):
                product_resolver.resolve_items(
                    [
                        {"display_name": "Desk Organizer", "search_term": "desk organizer"},
                        {"display_name": "Pen Holder", "search_term": "pen holder"},
                    ],
                    api_token="fake-token",
                    cache={},
                    allow_unresolved=True,
                )
        finally:
            product_resolver._search_amazon = original_search

        self.assertEqual(calls, ["desk organizer"])


class TestAffiliateLinks(unittest.TestCase):
    def setUp(self):
        self.aff = affiliate_mod.Affiliate(fresh_cfg())

    def test_asin_link(self):
        url = self.aff.product_url("B0CHX1W1XY", "power bank")
        self.assertEqual(url, "https://www.amazon.com/dp/B0CHX1W1XY?tag=testtag-20")

    def test_invalid_asin_does_not_fall_back_to_search(self):
        self.assertEqual(self.aff.product_url("12345", "yoga mat"), "")

    def test_missing_asin_does_not_create_search_link(self):
        self.assertEqual(
            self.aff.url_for({"name": "Yoga Mat", "search_term": "best yoga mat for beginners"}),
            "",
        )

    def test_rel_attributes_present_on_direct_product_link(self):
        html = self.aff.product_box({"name": "Yoga Mat", "asin": "B0ABCDEFG1"})
        self.assertIn('href="https://www.amazon.com/dp/B0ABCDEFG1?tag=testtag-20"', html)
        self.assertIn('rel="nofollow sponsored noopener"', html)
        self.assertIn('target="_blank"', html)

    def test_disclosure_is_amazon_compliant(self):
        self.assertIn("affiliate", self.aff.disclosure_block().lower())


class TestTemplateEngine(unittest.TestCase):
    def test_all_three_shapes_build(self):
        cfg = fresh_cfg()
        topics = topic_planner.next_topics(state=util.load_state(), count=40)
        seen = set()
        for topic in topics:
            if topic["template"] in seen:
                continue
            seen.add(topic["template"])
            article = template_engine.build_topic_data(topic, cfg)
            self.assertTrue(article["title"])
            self.assertGreaterEqual(len(article["sections"]), 4)
            self.assertGreaterEqual(len(article.get("audience_fit", [])), 2)
            self.assertGreaterEqual(len(article["faq"]), 3)
        self.assertEqual(seen, set(topic_planner.TEMPLATES))


class TestAIPath(unittest.TestCase):
    """The AI path is exercised with a fake provider - no network."""

    GOOD = {
        "title": "Test Product Review: What I Would Check Before Buying",
        "meta_description": "A meta description that is definitely long enough to be useful here.",
        "intro": ["First intro paragraph with enough words to be meaningful.", "Second intro paragraph.", "Third intro paragraph."],
        "audience_fit": ["This suits a buyer who needs the described features.", "It may not suit a buyer with different requirements."],
        "sections": [
            {"heading": f"Section {i}", "paragraphs": [" ".join(["word"] * 60)], "bullets": ["a", "b", "c"] if i % 2 else []}
            for i in range(1, 7)
        ],
        "specs": [{"label": "Warranty", "value": "Check the terms"}],
        "pros": ["one", "two", "three", "four"],
        "cons": ["bad one", "bad two", "bad three"],
        "comparison_rows": [],
        "faq": [{"q": f"Question {i}?", "a": " ".join(["answer"] * 50)} for i in range(1, 5)],
        "conclusion": ["Closing paragraph one.", "Closing paragraph two."],
    }

    def test_extract_json_handles_fences_and_prose(self):
        self.assertEqual(providers.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(providers.extract_json('Sure! Here you go:\n{"a": 1}\nHope that helps'), {"a": 1})
        self.assertEqual(providers.extract_json('{"a": 1,}'), {"a": 1})   # trailing comma

    def test_extract_json_never_returns_a_non_dict(self):
        """Regression: a model answering `[{...}]` used to reach normalise() as a
        list, which raised AttributeError. AttributeError is NOT a RuntimeError,
        so generate.run()'s catch-all missed it and the whole GitHub Actions job
        died with "Process completed with exit code 1" mid-batch.
        """
        # a one-element array is unwrapped, not rejected
        self.assertEqual(providers.extract_json('[{"a": 1}]'), {"a": 1})
        self.assertEqual(providers.extract_json('```json\n[{"a": 1}]\n```'), {"a": 1})
        self.assertEqual(providers.extract_json('prose [{"a": 1}] prose'), {"a": 1})
        # anything with no object in it must raise AIError (handled upstream),
        # never return something the rest of the pipeline would choke on
        for junk in ('[1, 2, 3]', '[]', '"a string"', '42', 'null', 'true'):
            with self.assertRaises(providers.AIError, msg=junk):
                providers.extract_json(junk)

    def test_normalise_survives_any_garbage_input(self):
        """normalise() is the last line of defence - it must not raise."""
        topic = {"keyword": "test product", "title": "Test Product Review", "slug": "x"}
        for junk in ([], [{"title": "T"}], "text", None, 42, 3.5, True):
            article = normalise(junk, topic)
            self.assertIsInstance(article, dict)
            self.assertEqual(article["title"], "Test Product Review")

    def test_a_crashing_ai_provider_cannot_kill_the_batch(self):
        """Regression: an AttributeError from the AI path used to escape both
        write_article() and generate.run()'s `except (AIError, RuntimeError)`,
        killing the whole job. Now every exception is either converted to
        AIError or swallowed by the template fallback.
        """
        def explode(*args, **kwargs):
            raise AttributeError("'list' object has no attribute 'get'")

        original = ai_writer.generate_json
        ai_writer.generate_json = explode
        try:
            cfg = fresh_cfg()
            os.environ["GROQ_API_KEY"] = "fake"
            topic = {"keyword": "test widget", "title": "Test Widget Review",
                     "slug": "crash-test", "template": "review", "niche": "kitchen",
                     "items": [{"name": "Test Widget", "display_name": "Test Widget",
                                "search_term": "test widget", "asin": "B0TEST0001",
                                "asin_verified": True, "verified_product_title": "Test Widget"}]}
            try:
                ai_writer.write_article(topic, cfg)
            except providers.AIError:
                pass                        # converted to AIError -> caller falls back
            except AttributeError as exc:   # the bug: this escaped and killed the job
                self.fail(f"AttributeError escaped the writer: {exc}")
        finally:
            ai_writer.generate_json = original

    def test_generate_one_degrades_to_template_when_the_ai_crashes(self):
        """End to end: a crashing provider still produces a publishable article."""
        import generate as gen

        def explode(*args, **kwargs):
            raise AttributeError("'list' object has no attribute 'get'")

        original = ai_writer.generate_json
        original_resolver = gen.resolve_topic_items
        ai_writer.generate_json = explode

        def attach_verified_test_asins(topic):
            for index, item in enumerate(topic.get("items", []), start=1):
                item["asin"] = f"B0TEST{index:04d}"
                item["asin_verified"] = True
                item["verified_product_title"] = item.get("display_name") or item.get("name")
            return topic

        gen.resolve_topic_items = attach_verified_test_asins
        try:
            cfg = fresh_cfg()
            cfg["content"]["fallback_to_template"] = True
            os.environ["GROQ_API_KEY"] = "fake"
            state = {"generated": [], "published": [], "queued_slugs": [], "keywords_used": {},
                     "last_publish_ts": None, "failures": {"count": 0, "until": None, "last_error": None},
                     "counters": {"total_generated": 0, "total_published": 0}, "titles_used": []}
            topics = topic_planner.next_topics(state=state, count=1)
            self.assertTrue(topics)
            result = gen.generate_one(cfg, state, topics[0], mode="auto")
            self.assertIsNotNone(result, "generate_one returned None after an AI crash")
            self.assertEqual(result["source"], "template")
        finally:
            ai_writer.generate_json = original
            gen.resolve_topic_items = original_resolver

    def test_normalise_locks_the_planned_title(self):
        raw = dict(self.GOOD, title="CLICKBAIT: You Will Not Believe This")
        topic = {"keyword": "test product", "title": "Test Product Review: The Planned Headline", "slug": "x"}
        self.assertEqual(normalise(raw, topic)["title"], "Test Product Review: The Planned Headline")

    def test_validate_rejects_fabrications(self):
        cfg = fresh_cfg()
        bad = json.loads(json.dumps(self.GOOD))
        bad["sections"][0]["paragraphs"][0] += " It costs $199 and rates 4.5/5 after 40 hours of testing."
        ok, problems = validate(normalise(bad, {"keyword": "x", "title": "x" * 30, "slug": "x"}), cfg)
        self.assertFalse(ok)
        self.assertTrue(any("price" in p or "rating" in p or "test claim" in p for p in problems), problems)

    def test_validate_rejects_thin_content(self):
        cfg = fresh_cfg()
        thin = json.loads(json.dumps(self.GOOD))
        thin["sections"] = thin["sections"][:1]
        ok, problems = validate(normalise(thin, {"keyword": "x", "title": "x" * 30, "slug": "x"}), cfg)
        self.assertFalse(ok)
        self.assertTrue(any("short" in p or "sections" in p for p in problems), problems)

    def test_provider_fallback_chain(self):
        calls = []

        def boom(cfg, system, user, temp, tokens):
            calls.append(cfg.get("model"))
            raise providers.AIError("quota exhausted")

        def works(cfg, system, user, temp, tokens):
            calls.append(cfg.get("model"))
            return json.dumps(self.GOOD)

        providers.PROVIDERS["groq"] = boom
        providers.PROVIDERS["mistral"] = works
        for name, key in [("groq", "GROQ_API_KEY"), ("mistral", "MISTRAL_API_KEY")]:
            os.environ[key] = "fake"
        ai_cfg = fresh_cfg()["ai"]
        parsed, used = providers.generate_json(ai_cfg, "sys", "usr")
        self.assertEqual(used, "mistral")
        self.assertEqual(calls, ["openai/gpt-oss-120b", "mistral-large-latest"])
        self.assertEqual(parsed["title"], self.GOOD["title"])


class TestQualityGate(unittest.TestCase):
    def test_rejects_thin_content(self):
        cfg = fresh_cfg()
        ok, reasons = safety.quality_gate("<p>too short</p>", {}, cfg)
        self.assertFalse(ok)
        self.assertTrue(any("thin" in r for r in reasons))

    def test_rejects_missing_disclosure(self):
        cfg = fresh_cfg()
        body = "<h2>x</h2>" + "<p>" + " ".join(["word"] * 1400) + "</p>"
        ok, reasons = safety.quality_gate(body, {}, cfg)
        self.assertFalse(ok)
        self.assertTrue(any("disclosure" in r for r in reasons))

    def test_rejects_repeated_sentences(self):
        cfg = fresh_cfg()
        sentence = "This exact same sentence repeats over and over in the article body. "
        body = "<h2>x</h2><p>" + sentence * 6 + "</p><div class='disclosure'>Affiliate disclosure.</div>"
        body += "<p>" + " ".join(["filler"] * 1200) + "</p>"
        ok, reasons = safety.quality_gate(body, {}, cfg)
        self.assertFalse(ok)
        self.assertTrue(any("repeated" in r for r in reasons))

    def test_allows_owner_supplied_prices_in_tables(self):
        cfg = fresh_cfg()
        table = '<table class="spec-table"><tr><td>&pound;149</td></tr></table>'
        badge = '<span class="pb-badge" data-owner-data="1">under $50</span>'
        body = "<h2>x</h2>" + table + badge + "<p>" + " ".join(["word"] * 1400) + "</p>"
        body += "<div class='disclosure'>Affiliate disclosure here.</div>"
        ok, reasons = safety.quality_gate(body, {}, cfg)
        self.assertTrue(ok, reasons)


class TestAffiliateTagGuard(unittest.TestCase):
    def test_placeholder_tag_is_detected(self):
        cfg = fresh_cfg()
        cfg["affiliate"]["tag"] = "YOURTAG-20"
        from generate import affiliate_tag_ready
        self.assertFalse(affiliate_tag_ready(cfg))

    def test_real_tag_passes(self):
        cfg = fresh_cfg()
        from generate import affiliate_tag_ready
        self.assertTrue(affiliate_tag_ready(cfg))
        cfg["affiliate"]["tag"] = ""
        self.assertFalse(affiliate_tag_ready(cfg))


    def test_queue_metadata_records_asin_verification(self):
        import inspect
        source = inspect.getsource(render_mod.save_article)
        self.assertIn('"asin_verified": i.get("asin_verified") is True', source)
        self.assertIn('"verified_product_title": i.get("verified_product_title", "")', source)

    def test_article_archive_has_no_category_hub_links(self):
        import inspect
        source = inspect.getsource(render_mod.render_markdown)
        self.assertNotIn("Browse All Categories", source)
        self.assertNotIn("/p/categories.html", source)

class TestEndToEnd(unittest.TestCase):
    def test_offline_pipeline_produces_publishable_html(self):
        cfg = fresh_cfg()
        # Keep this fixture focused on rendering/link correctness; the template
        # writer may vary slightly in length across planned topic families.
        cfg["content"]["min_words"] = 500
        state = util.load_state()
        topics = topic_planner.next_topics(state=state, count=6)
        self.assertTrue(topics)
        aff = affiliate_mod.Affiliate(cfg)
        made = []
        for topic_index, topic in enumerate(topics, start=1):
            for item_index, item in enumerate(topic["items"], start=1):
                asin = f"B0TEST{topic_index:02d}{item_index:02d}"
                item["asin"] = asin
                item["asin_verified"] = True
                item["verified_product_title"] = item.get("display_name") or item.get("name")
            article = template_engine.build_topic_data(topic, cfg)
            hero = "<figure class='hero hero-svg'><svg></svg></figure>"
            html = render_mod.render_html(article, topic, cfg, aff, hero)
            ok, reasons = safety.quality_gate(html, article, cfg, expected_product_count=len(topic["items"]))
            self.assertTrue(ok, f"{topic['slug']}: {reasons}")
            path = render_mod.save_article(article, topic, cfg, aff, hero)
            made.append((topic, html, path))

        # every rendered post must carry the things Blogger/Amazon/Google need
        for topic, html, path in made:
            self.assertNotIn("<style", html.lower())
            self.assertNotIn("application/ld+json", html)
            self.assertEqual(html.count("View Product on Amazon"), len(topic["items"]))
            self.assertNotIn("/s?k=", html)
            self.assertIn("tag=testtag-20", html)
            self.assertIn("nofollow sponsored noopener", html)
            self.assertIn("As an Amazon Associate", html)
            self.assertIn("Who May Find It Useful?", html)
            self.assertIn("Final Thoughts", html)
            self.assertIn('<h2 style="background-color: #f1f3f4;', html)
            self.assertIn("<h2", html)
            self.assertTrue(path.exists())
            self.assertTrue(path.with_suffix(".md").exists())

        # index.json is what the Apps Script publisher consumes
        index = json.loads((util.QUEUE_DIR / "index.json").read_text())
        self.assertEqual(index["count"], len(made))
        for entry in index["posts"]:
            self.assertTrue(entry["title"] and entry["html_file"] and entry["items"])

    def test_queue_pruning_keeps_repo_bounded(self):
        cfg = fresh_cfg()
        removed = render_mod.prune_queue(3)
        remaining = [p for p in util.QUEUE_DIR.glob("*.json") if p.name != "index.json"]
        self.assertLessEqual(len(remaining), 3)
        self.assertGreaterEqual(removed, 0)


class TestCircuitBreaker(unittest.TestCase):
    def test_breaker_opens_after_repeated_failures(self):
        cfg = fresh_cfg()
        if util.STATE_PATH.exists():
            util.STATE_PATH.unlink()
        closed, _ = safety.breaker_closed(cfg)
        self.assertFalse(closed)
        for _ in range(cfg["safety"]["max_consecutive_failures"]):
            safety.record_failure(cfg, "simulated Blogger API 403")
        closed, reason = safety.breaker_closed(cfg)
        self.assertTrue(closed, "breaker should have opened")
        self.assertIn("simulated", reason)
        state = util.load_state()
        state["failures"] = {"count": 0, "until": None, "last_error": None}
        util.save_state(state)
        closed, _ = safety.breaker_closed(cfg)
        self.assertFalse(closed, "breaker should close after reset")


def teardown() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    successful = False
    try:
        program = unittest.main(verbosity=2, exit=False)
        successful = bool(program.result and program.result.wasSuccessful())
    finally:
        teardown()
    raise SystemExit(0 if successful else 1)
