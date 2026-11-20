import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from enhance_published_articles import (
    MARKER,
    enhance_html,
    extract_anchor_hrefs,
    strip_non_amazon_links,
    validate_additions,
)


def sample_additions():
    return {
        "quick_take": "Choose by the main job you need the product to perform, then check fit and day-to-day handling before deciding.",
        "sections": [
            {
                "heading": "A More Practical Way to Compare the Options",
                "paragraphs": [
                    "Start by identifying the task that matters most in your own routine, rather than treating every listed feature as equally important. This keeps the comparison tied to a real use case.",
                    "Next, consider where and how often the item will be used. The article explains the available details, while the current product listing can confirm any specification that is not covered here.",
                ],
                "bullets": [
                    "Write down the main task you expect the product to handle.",
                    "Compare the article's stated features against that task.",
                    "Confirm missing fit or compatibility details before ordering.",
                ],
            },
            {
                "heading": "Checks to Make Before You Choose",
                "paragraphs": [
                    "A sensible final check is to compare the product's stated requirements with your own setup. If an important measurement or compatibility detail is not included in the article, do not assume that it will fit.",
                    "Think about storage, cleaning, installation, and the amount of attention the item may need. These practical points can matter as much as the headline feature when two options appear similar.",
                ],
                "bullets": [
                    "Verify dimensions and compatibility where they matter.",
                    "Check what installation or maintenance the listing describes.",
                    "Choose the option that fits your routine rather than the longest feature list.",
                ],
            },
        ],
        "faq": [
            {
                "question": "What should I compare first?",
                "answer": "Start with the task you need the product to handle most often. Then compare the supported features and check any missing fit details in the current product information.",
            },
            {
                "question": "Should I rely on details that are not listed here?",
                "answer": "No. Treat missing measurements, compatibility notes, and included accessories as items to verify in the current product listing before making a decision.",
            },
            {
                "question": "How can I choose between similar options?",
                "answer": "Prioritize the use case that matters most to you, then compare setup, maintenance, fit, and everyday handling. A shorter feature list may still suit your needs better.",
            },
        ],
    }


class PublishedEnhancementTests(unittest.TestCase):
    def test_validation_accepts_structured_buyer_value(self):
        result = validate_additions(sample_additions())
        self.assertEqual(len(result["sections"]), 2)
        self.assertEqual(len(result["faq"]), 3)
        self.assertGreaterEqual(result["word_count"], 210)

    def test_non_amazon_links_are_unwrapped_but_amazon_links_remain(self):
        source = (
            '<p>Read <a href="https://example.com/guide">the guide</a> first.</p>'
            '<p><a href="https://www.amazon.com/dp/B012345678?tag=hotr0e-20">View on Amazon</a></p>'
        )
        cleaned = strip_non_amazon_links(source)
        self.assertNotIn('href="https://example.com/guide"', cleaned)
        self.assertIn("the guide", cleaned)
        self.assertIn('href="https://www.amazon.com/dp/B012345678?tag=hotr0e-20"', cleaned)

    def test_enhancement_keeps_existing_structure_and_amazon_href_exactly(self):
        amazon = 'href="https://www.amazon.com/dp/B012345678?tag=hotr0e-20"'
        source = (
            '<p>Intro paragraph one.</p><p>Intro paragraph two.</p><p>Intro paragraph three.</p>'
            '<p><em>Disclosure remains.</em></p>'
            '<h2 style="existing">Original product section</h2><p>Original section copy.</p>'
            '<div class="amazon-cta-group"><a class="amazon-cta" ' + amazon + '>View on Amazon</a></div>'
            '<h2>Who May Find It Useful?</h2><p>Original audience paragraph.</p>'
            '<h2>Final Thoughts</h2><p>Original conclusion.</p>'
            '<div class="amazon-cta-group"><a class="amazon-cta" ' + amazon + '>View on Amazon</a></div>'
        )
        updated = enhance_html(source, validate_additions(sample_additions()))
        self.assertTrue(updated.startswith(MARKER))
        self.assertIn('<h2 style="existing">Original product section</h2>', updated)
        self.assertIn("<p>Original conclusion.</p>", updated)
        self.assertIn("Quick Take", updated)
        self.assertIn("Practical Questions", updated)
        self.assertEqual(extract_anchor_hrefs(source), extract_anchor_hrefs(updated))
        self.assertTrue(all("amazon.com" in href for href in extract_anchor_hrefs(updated)))

    def test_enhancement_is_idempotent(self):
        source = '<p>One.</p><p>Two.</p><p>Three.</p><h2>Final Thoughts</h2><p>End.</p>'
        first = enhance_html(source, validate_additions(sample_additions()))
        second = enhance_html(first, validate_additions(sample_additions()))
        self.assertEqual(first, second)

    def test_invalid_generated_link_is_rejected(self):
        additions = sample_additions()
        additions["faq"][0]["answer"] = "Read https://example.com before making this decision."
        with self.assertRaises(ValueError):
            validate_additions(additions)


if __name__ == "__main__":
    unittest.main()
