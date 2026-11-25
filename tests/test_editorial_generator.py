import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import editorial_generator as eg


class EditorialGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.topic = {
            "slug": "monitor-arm-vs-nightstand-test",
            "title": "Monitor Arm vs Nightstand: What Makes a Better Small-Space Workstation?",
            "keyword": "monitor arm vs nightstand",
            "niche": "home office",
            "editorial_brief": "Compare the general setup roles without recommending a specific product.",
        }
        self.article = {
            "title": self.topic["title"],
            "meta_description": "Compare a monitor arm and a nightstand as workspace choices, focusing on stability, reach, desk space, and practical checks before changing a setup.",
            "intro": [
                "A monitor arm and a nightstand solve different workspace problems, so the better choice depends on whether the goal is to position a screen or create a surface beside a bed.",
                "A nightstand is furniture intended for nearby storage and occasional reach, while a monitor arm is a mounting arrangement that must match the screen and supporting surface.",
                "Treat them as different categories rather than interchangeable supports, and check the stability and intended use of any setup before placing a screen above a work area.",
            ],
            "sections": [
                {"heading": f"Decision point {i}", "paragraphs": [
                    "Start by identifying the job the setup needs to perform. A screen support has to hold the display in a controlled position, while a side table is mainly useful for placing everyday items within reach. The shape of a room does not change those basic roles.",
                    "Check the surface, available clearance, and how often the arrangement will be adjusted. Avoid assuming that a piece of furniture can safely carry equipment just because it appears wide or solid. Use the manufacturer's stated limits for any load-bearing component.",
                ]} for i in range(1, 7)
            ],
            "audience_fit": [
                "This guide is useful when planning a compact home office or deciding how to separate work equipment from bedside furniture.",
                "It is also useful for readers who need to assess an existing arrangement before moving a display or changing the room layout.",
            ],
            "conclusion": [
                "Choose the arrangement by its intended function, confirmed support capacity, and the space available for safe movement. Do not treat unrelated furniture as a substitute for a purpose-designed mount.",
                "Measure the room, review the relevant product documentation, and test the layout without placing valuable equipment in an unstable position.",
            ],
            "faq": [
                {"q": "Can a nightstand replace a monitor arm?", "a": "They serve different purposes. Check the suitability and load capacity of any surface before placing a display on it."},
                {"q": "What should be checked first?", "a": "Start with the screen support method, the stability of the desk or furniture, and the manufacturer's stated limits."},
                {"q": "Does a small room change the decision?", "a": "It changes the layout constraints, but it does not remove the need for a stable and suitable support."},
            ],
        }

    def test_editorial_html_has_no_links_or_affiliate_ctas(self):
        body = eg.render_html(self.article)
        self.assertNotRegex(body, r"<a\b")
        self.assertNotIn("amazon-cta", body)
        self.assertNotIn("affiliate disclosure", body.lower())
        self.assertIn("<h2", body)
        self.assertIn("Final Thoughts", body)

    def test_html_escapes_model_markup(self):
        self.article["intro"][0] = "<script>alert('x')</script>"
        body = eg.render_html(self.article)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_metadata_is_separate_non_publishable_draft(self):
        body = eg.render_html(self.article)
        meta = eg._metadata(self.article, self.topic, "groq", body)
        self.assertEqual(meta["items"], [])
        self.assertTrue(meta["editorial_only"])
        self.assertTrue(meta["draft"])
        self.assertTrue(meta["review_required"])
        self.assertFalse(meta["product_links_verified"])
        self.assertFalse(meta["publishable"])
        self.assertEqual(meta["layout_version"], 0)

    def test_topic_selection_skips_previous_drafts_and_caps_batch(self):
        topics = [dict(self.topic, slug=f"topic-{i}") for i in range(5)]
        state = {"generated_slugs": ["topic-0", "topic-2"]}
        selected = eg.select_topics(topics, state, 10)
        self.assertEqual([x["slug"] for x in selected], ["topic-1", "topic-3", "topic-4"])
        self.assertLessEqual(len(eg.select_topics(topics, state, 100)), eg.MAX_BATCH)


if __name__ == "__main__":
    unittest.main()
