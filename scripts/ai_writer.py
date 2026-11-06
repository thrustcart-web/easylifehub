"""AI writer: prompts, validation and normalisation of model output.

The prompt is engineered for one thing above all: output that is *publishable
without a human reading it*. That means:

  * strict JSON schema (so parsing never fails silently)
  * the headline is assigned by the planner, not invented by the model - that is
    what stops 300 posts from sharing one title formula
  * explicit bans on fabricated prices, ratings, fake lab data and hype words
  * practical, evidence-based buyer guidance without fabricated product claims
"""
from __future__ import annotations

import json
import random
import re

from providers import AIError, available_providers, generate_json
from util import log

REQUIRED_FIELDS = ("title", "intro", "sections", "audience_fit", "conclusion")

BANNED_PHRASES = [
    "in today's fast-paced world",
    "in the world of",
    "look no further",
    "game-changer",
    "game changer",
    "revolutionize",
    "revolutionise",
    "unlock the power",
    "dive into",
    "delve into",
    "tapestry",
    "elevate your",
    "seamless",
    "robust solution",
    "it's important to note",
    "in conclusion",
    "when it comes to",
    "a testament to",
    "navigating the",
    "embark on",
]

HYPE_RE = re.compile(r"\b(best ever|world's #1|guaranteed|miracle|perfect for everyone|100% risk-free)\b", re.I)
PRICE_RE = re.compile(r"(?<![\w])(?:\$|USD\s?|£|€)\s?\d+(?:\.\d{2})?")
RATING_RE = re.compile(r"\b\d(?:\.\d)?\s?(?:/|out of)\s?5\b", re.I)
CUSTOMER_REVIEW_RE = re.compile(r"\b(?:customer|buyer|user)\s+(?:reviews?|ratings?)\b|\b\d[\d,]*\s+(?:customer\s+)?reviews?\b|\b(?:star\s+ratings?|review\s+counts?)\b", re.I)
FAKE_TEST_RE = re.compile(
    r"\b(?:we (?:tested|measured|ran|used) it for|in our (?:lab|tests)|after \d+ (?:hours|days|weeks) of testing)\b", re.I
)
URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(https?://[^)]+\)", re.I)


# ----------------------------------------------------------------- prompts --
def _template_brief(template: str, items: list[dict]) -> str:
    names = ", ".join((i.get("display_name") or i.get("name", "")) for i in items)
    if template == "comparison":
        a, b = items[0], items[1]
        return (
            f"ARTICLE TYPE: head-to-head comparison.\n"
            f"Compare exactly these two products: (A) {a.get('display_name')} and (B) {b.get('display_name')}.\n"
            f"Use 4-6 useful sections comparing verified product features, setup, compatibility, practical use and ownership considerations. Do not add a pros/cons list.\n"
            f"Put audience suitability in audience_fit and reserve conclusion for Final Thoughts.\n"
            f"Available context: {names}"
        )
    if template == "bestof":
        return (
            f"ARTICLE TYPE: curated product roundup.\n"
            f"Cover these options, one focused section each, in this order: {names}.\n"
            f"Use 4-6 useful sections covering verified features, setup, practical use and ownership considerations. Do not add a pros/cons list.\n"
            f"Put audience suitability in audience_fit and reserve conclusion for Final Thoughts.\n"
            f"Do not invent product weaknesses or unsupported comparisons."
        )
    return (
        f"ARTICLE TYPE: single-product review.\n"
        f"Subject: {items[0].get('display_name') if items else ''}.\n"
        f"Use 4-6 useful sections covering verified features, design, operation, installation/setup and maintenance. Do not add a pros/cons list.\n"
        f"Put audience suitability in audience_fit and reserve conclusion for Final Thoughts.\n"
        f"Available context: {names}"
    )

def _voice_variation(seed: int) -> str:
    voices = [
        "Voice: a practical buyer-guide editor. Concrete, measured and useful; never claims personal ownership or testing.",
        "Voice: a careful product researcher who separates known facts from points buyers should verify.",
        "Voice: a methodical reviewer who explains who each option suits without inventing test results.",
        "Voice: a consumer guide writer who is precise about evidence and avoids marketing language.",
        "Voice: a category specialist explaining product-selection criteria in plain English.",
        "Voice: an independent editorial analyst who states uncertainty instead of guessing.",
    ]
    styles = [
        "Sentence rhythm: mostly short. One long sentence per section, maximum.",
        "Sentence rhythm: mixed. Open several sections with a single blunt statement.",
        "Sentence rhythm: conversational, as if explaining across a table. No rhetorical questions.",
        "Sentence rhythm: clipped and declarative. Prefer specifics over adjectives.",
        "Openings: never restate the title. Start mid-thought, with the most useful fact.",
    ]
    rng = random.Random(seed)
    return rng.choice(voices) + " " + rng.choice(styles)


SYSTEM_PROMPT = """You are a senior product reviewer for an independent buyer's-guide blog.
You write clear, useful buyer guides for readers who want accurate product information without sales hype.

HARD RULES - violating any of these makes the article unusable and it will be rejected automatically:
1. Output ONLY one valid JSON object. No markdown fences, no text before or after.
2. NEVER invent product facts: prices, ratings, sales ranks, measurements, battery numbers, materials,
   included components, ports, compatibility, warranty terms, defects, performance or test results.
   A product-specific fact may be stated only when it appears in supplied topic data or verified source notes.
3. If the supplied context does not confirm a feature, do not guess from the product category or model name.
   Turn the unknown into a practical buyer check ("verify the supported ports on the current manufacturer listing").
4. Never imply personal ownership, hands-on testing, lab work, or first-hand experience.
5. Do not include product prices, price ranges, discounts, star ratings, review counts, customer opinions, customer-review summaries, or claims about what buyers say. These details are not needed.
6. Do not invent weaknesses, defects, drawbacks, or negative experiences. Explain practical fit and what a buyer should verify instead.\n7. Never use these words or phrases: {banned}.
8. No hype, no exclamation marks, no flattery of the reader.
9. Be specific only when the evidence supports it; otherwise explain the decision criterion clearly.
10. Address the reader as "you" at most twice per section.
11. Do not write an introduction that restates the title.
12. Body copy: {min_words}-{target_words} words. Substance, not padding. The three-paragraph intro, main sections, audience_fit and conclusion are mandatory.
13. Vary paragraph length. Some sections get two sentences, some get five.
14. The first introduction paragraph must directly answer the reader's central buying question in plain language (about 35-60 words), before adding context.
15. Each main section's first paragraph must answer or resolve the question implied by its heading; use natural question-led headings when helpful.
16. Cover the real intent behind the topic, including who the product suits, what to compare, compatibility checks and ownership considerations. Do not repeat the same keyword unnaturally.
17. Write for people first and make answers clear enough to stand alone when quoted by answer engines. This is useful structure, not a promise of AI citations or rankings.
18. Write a unique meta description of 145-160 characters that accurately summarizes the article and includes the topic naturally.
19. Use the REQUIRED TITLE exactly as given. Do not rewrite it, do not add a year.
20. Never include hyperlinks, internal links, external links, bare URLs, Markdown links, HTML anchors, or a Table of Contents. Amazon links are inserted later by the renderer as green buttons; do not write any link yourself.

JSON SCHEMA (exact keys):
{{
  "title": "the REQUIRED TITLE, verbatim",
  "meta_description": "string, 145-160 chars",
  "intro": ["opening paragraph 1", "opening paragraph 2", "opening paragraph 3"],
  "sections": [
    {{"heading": "product-specific heading, 4-12 words",
     "paragraphs": ["two or three detailed paragraphs"],
     "bullets": []}}
  ],
  "audience_fit": ["paragraph about who may find this useful", "second practical audience-fit paragraph"],
  "conclusion": ["Final Thoughts paragraph 1", "Final Thoughts paragraph 2"]
}}

Notes:
- The renderer always applies the same visual template to every article.
- Return exactly 3 introduction paragraphs.
- Return 4-6 main feature, operation, design, setup or maintenance sections.
- Do not add an audience section or verdict inside "sections".
- Return exactly 2 paragraphs in "audience_fit" and 2 in "conclusion".
- Keep claims factual and product-specific. Do not invent specifications, prices,
  ratings, customer-review counts or personal testing experience.
- No links, URLs, HTML anchors, CSS, scripts, markdown headings, markdown link syntax, or Table of Contents.
- Everything in {language}.
"""


def build_prompts(topic: dict, cfg: dict) -> tuple[str, str]:
    content_cfg = cfg.get("content", {})
    language = content_cfg.get("language", "en")
    lang_name = {"en": "English", "ar": "Arabic", "es": "Spanish", "fr": "French", "de": "German"}.get(language, "English")
    items = topic.get("items", [])
    seed = abs(hash(topic["slug"])) % 10_000

    system = SYSTEM_PROMPT.format(
        banned=", ".join(f'"{b}"' for b in BANNED_PHRASES),
        language=lang_name,
        min_words=content_cfg.get("min_words", 501),
        target_words=content_cfg.get("target_words", 700),
    )

    fields = []
    for item in items:
        bits = [f"product: {item.get('display_name') or item.get('name')}"]
        verified_title = item.get("verified_product_title")
        if verified_title:
            bits.append(f"verified Amazon listing title (confirmed listing wording only): {verified_title}")
        for key in ("search_term", "asin", "niche", "feature", "audience", "notes"):
            value = item.get(key)
            if value:
                bits.append(f"{key}: {value}")
        fields.append(" | ".join(bits))

    planned_title = topic.get("title", "")
    user = f"""{_template_brief(topic['template'], items)}

REQUIRED TITLE (use verbatim as the "title" value): "{planned_title}"

{_voice_variation(seed)}

TOPIC DATA - this is the evidence currently available. Do not invent beyond it. You may explain general
category-selection principles, but clearly distinguish those principles from facts about the named product.
If a verified Amazon listing title is supplied, treat its wording as the only confirmed product-specific listing evidence; do not infer materials, dimensions, compatibility, performance, warranty, included accessories, safety, or test results from a name or category. If verified source notes are supplied, rely only on those notes for product-specific claims:
{chr(10).join(fields)}

Niche: {topic.get('niche', 'general')}

Return the JSON object now."""
    return system, user


# ------------------------------------------------------------ normalisation --
def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _clean_text(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"!\s*", ". ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text


def _strip_markdown(text: str) -> str:
    """Models sneak markdown into JSON strings. Convert the useful bits to HTML."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"^\s*[-*]\s+", "", text)
    return text.strip()


def normalise(raw, topic: dict) -> dict:
    """Coerce whatever the model returned into the article schema.

    Defensive on purpose: `raw` should already be a dict (providers.extract_json
    guarantees it), but a crash here kills the whole batch instead of one
    article, and AttributeError is not caught by the RuntimeError handlers.
    """
    if isinstance(raw, list):
        raw = next((x for x in raw if isinstance(x, dict)), {})
    if not isinstance(raw, dict):
        raw = {}
    keyword = topic.get("keyword", "")
    planned = (topic.get("title") or "").strip()

    title = _clean_text(raw.get("title") or "").strip('"')
    if planned:
        title = planned                      # the planner owns the headline
    if not title:
        title = f"{keyword.title()} Review: Worth Buying?"
    title = title[:95]

    sections = []
    for sec in raw.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        heading = _strip_markdown(_clean_text(sec.get("heading")))
        paragraphs = [_strip_markdown(_clean_text(p)) for p in _as_list(sec.get("paragraphs"))]
        bullets = [_strip_markdown(_clean_text(b)) for b in _as_list(sec.get("bullets"))]
        if not heading or not (paragraphs or bullets):
            continue
        sections.append({"heading": heading[:110], "paragraphs": paragraphs, "bullets": bullets})

    audience_fit = [_strip_markdown(_clean_text(p)) for p in _as_list(raw.get("audience_fit"))]

    faq = []
    for entry in raw.get("faq") or []:
        if not isinstance(entry, dict):
            continue
        q = _strip_markdown(_clean_text(entry.get("q") or entry.get("question")))
        a = _strip_markdown(_clean_text(entry.get("a") or entry.get("answer")))
        if q and a:
            faq.append({"q": q[:180], "a": a})

    specs = []
    for entry in raw.get("specs") or []:
        if isinstance(entry, dict) and entry.get("label"):
            specs.append(
                {
                    "label": _clean_text(entry["label"])[:60],
                    "value": _clean_text(entry.get("value", ""))[:220],
                }
            )

    comparison_rows = []
    for row in raw.get("comparison_rows") or []:
        if isinstance(row, dict) and row.get("criterion"):
            comparison_rows.append(
                {
                    "criterion": _clean_text(row["criterion"])[:80],
                    "a": _clean_text(row.get("a", ""))[:300],
                    "b": _clean_text(row.get("b", ""))[:300],
                }
            )

    return {
        "title": title,
        "meta_description": _clean_text(raw.get("meta_description"))[:320],
        "intro": [_strip_markdown(_clean_text(p)) for p in _as_list(raw.get("intro"))],
        "sections": sections,
        "audience_fit": audience_fit[:2],
        "specs": specs[:8],
        "pros": [_strip_markdown(_clean_text(p))[:160] for p in _as_list(raw.get("pros"))][:8],
        "cons": [_strip_markdown(_clean_text(p))[:160] for p in _as_list(raw.get("cons"))][:8],
        "comparison_rows": comparison_rows[:10],
        "faq": faq[:8],
        "conclusion": [_strip_markdown(_clean_text(p)) for p in _as_list(raw.get("conclusion"))],
        "_source": "ai",
    }


# ---------------------------------------------------------------- validation --
def validate(article: dict, cfg: dict) -> tuple[bool, list[str]]:
    """(ok, problems). ok=False blocks publishing."""
    problems: list[str] = []
    content_cfg = cfg.get("content", {})
    min_words = int(content_cfg.get("min_words", 501))

    for field in REQUIRED_FIELDS:
        if not article.get(field):
            problems.append(f"missing field: {field}")
    description = str(article.get("meta_description") or "").strip()
    if len(description) < 120 or len(description) > 170:
        problems.append(f"meta description length must be 120-170 characters (got {len(description)})")

    blob = json.dumps(article, ensure_ascii=False).lower()
    for phrase in BANNED_PHRASES:
        if phrase in blob:
            problems.append(f"banned phrase: {phrase}")
    if HYPE_RE.search(blob):
        problems.append("hype language detected")
    if PRICE_RE.search(blob):
        problems.append("fabricated price detected")
    if RATING_RE.search(blob):
        problems.append("fabricated rating detected")
    if FAKE_TEST_RE.search(blob):
        problems.append("fabricated test claim detected")
    if CUSTOMER_REVIEW_RE.search(blob):
        problems.append("customer review or rating claims are not allowed")
    if URL_RE.search(blob) or MARKDOWN_LINK_RE.search(blob) or re.search(r"<a\b", blob, re.I):
        problems.append("links or URLs are not allowed in article copy")

    words = len(re.findall(r"[\w'-]+", re.sub(r"<[^>]+>", " ", blob)))
    if words < min_words:
        problems.append(f"too short: {words} words < {min_words}")
    if len(article.get("intro", [])) != 3:
        problems.append("intro must contain exactly 3 paragraphs")
    if len(article.get("sections", [])) < 4:
        problems.append(f"only {len(article.get('sections', []))} main sections")
    if len(article.get("audience_fit", [])) < 2:
        problems.append("audience_fit must contain 2 paragraphs")
    if len(article.get("conclusion", [])) < 2:
        problems.append("Final Thoughts must contain 2 paragraphs")
    if not article.get("title") or len(article["title"]) < 15:
        problems.append("title missing or too short")

    return (not problems), problems


def write_article(topic: dict, cfg: dict) -> tuple[dict, str]:
    """AI-first writing with one pass through independent providers."""
    ai_cfg = cfg.get("ai", {})
    system, user = build_prompts(topic, cfg)
    active_providers = available_providers(ai_cfg)
    attempts = max(len(active_providers), 1)
    tried_providers: set[str] = set()
    last_problems: list[str] = []

    if active_providers:
        log.info("  AI failover order available: %s", " -> ".join(active_providers))
    else:
        log.warning("  No AI provider API keys are configured in this workflow.")

    for attempt in range(1, attempts + 1):
        try:
            raw, provider, attempted = generate_json(
                ai_cfg, system, user,
                timeout_note=f"(provider pass {attempt}/{attempts})",
                exclude_providers=tried_providers,
                return_attempted=True,
            )
            tried_providers.update(attempted)
        except AIError as exc:
            tried_providers.update(getattr(exc, "attempted_providers", []))
            # generate_json has already tried every remaining configured source.
            # _post_json handles short transient retries; do not repeat a quota-
            # exhausted provider chain for the same article.
            raise
        except Exception as exc:  # noqa: BLE001 - provider wrapper must never crash the batch
            log.warning("  AI provider wrapper crashed: %s: %s",
                        type(exc).__name__, str(exc)[:160])
            if attempt == attempts:
                raise AIError(f"{type(exc).__name__}: {exc}") from exc
            continue

        try:
            article = normalise(raw, topic)
            ok, problems = validate(article, cfg)
        except Exception as exc:  # noqa: BLE001 - malformed output is one provider's failure
            log.warning("  could not read model reply from %s: %s: %s",
                        provider, type(exc).__name__, str(exc)[:160])
            last_problems = [f"unusable model reply: {type(exc).__name__}: {exc}"]
            continue

        if ok:
            article["_provider"] = provider
            return article, provider

        last_problems = problems
        log.warning("  validation failed from %s: %s",
                    provider, "; ".join(problems[:5]))
        user += (
            "\n\nYour previous attempt was rejected for: "
            + "; ".join(problems[:8])
            + "\nFix every one of those and return the corrected JSON object only."
        )
        # Next iteration excludes the provider that returned invalid content.

    raise AIError("AI output failed validation: " + "; ".join(last_problems[:6]))
