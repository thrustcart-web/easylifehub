#!/usr/bin/env python3
"""Generate stock-independent editorial drafts for Honest Trove.

These articles are stored outside content/queue and are never eligible for the
Amazon affiliate publisher. They contain no product ASINs, affiliate buttons,
stock claims, or external links.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from ai_writer import AIError, normalise, validate
from providers import available_providers, generate_json
from util import load_config, log, slugify, stable_id, word_count

TOPICS_PATH = ROOT / "data" / "editorial_topics.json"
OUT_DIR = ROOT / "content" / "editorial-drafts"
STATE_PATH = OUT_DIR / "state.json"
INDEX_PATH = OUT_DIR / "index.json"
MAX_BATCH = 3
MIN_EDITORIAL_WORDS = 650


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def load_topics() -> list[dict]:
    rows = _load_json(TOPICS_PATH, [])
    if not isinstance(rows, list):
        raise ValueError("data/editorial_topics.json must contain a JSON array.")
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        keyword = str(row.get("keyword") or "").strip()
        if not title or not keyword:
            continue
        slug = str(row.get("slug") or slugify(title, 55) + "-" + stable_id(title.lower()))
        if slug in seen:
            continue
        seen.add(slug)
        out.append({
            "slug": slug,
            "title": title,
            "keyword": keyword,
            "niche": str(row.get("niche") or "general").strip(),
            "editorial_brief": str(row.get("editorial_brief") or "").strip(),
        })
    return out


def select_topics(topics: list[dict], state: dict, count: int) -> list[dict]:
    done = set(state.get("generated_slugs") or [])
    return [topic for topic in topics if topic["slug"] not in done][:max(0, min(int(count), MAX_BATCH))]


def _prompts(topic: dict) -> tuple[str, str]:
    system = """You are a careful editor writing practical, evidence-conscious informational guides for Honest Trove.
Return exactly one valid JSON object and nothing else. Use plain English.

This is an EDITORIAL article, not an affiliate product review. Do not name or recommend specific products,
brands, ASINs, sellers, marketplaces, or retailers. Do not include any affiliate links, external links,
URLs, stock or availability claims, prices, discounts, star ratings, customer reviews, unsupported specs,
invented measurements, personal testing, first-hand experience, or claims that readers should buy a
particular item. Do not use a table of contents. Do not invent safety certifications or technical limits.
When a reader must verify a detail, explain what to check in the manufacturer's documentation without
naming a manufacturer or linking to a site.

The article must answer the exact search intent in the required title. Explain practical trade-offs,
decision criteria, limitations, and cautious checks. Distinguish general guidance from facts that vary
by model, room, mounting surface, or user. Never claim that every item in a category has the same design,
materials, mounting interface, load rating, or stability. Do not state that a household object is always
unsafe or always unsuitable; say that its suitability cannot be assumed unless its documentation confirms
the intended use and load. Do not generalize about common materials or maximum loads without evidence.
Avoid claims that a setup causes or prevents a medical condition. For screen supports, never encourage
unsecured displays, improvised elevated mounting, unstable stacking, or bypassing manufacturer limits.
Use qualified language where designs vary, and turn unknown measurements or ratings into checks rather
than invented facts.

Write 750-950 words of useful body copy, not filler. The first paragraph must directly answer the central
question. Use exactly 3 introduction paragraphs, at least 5 substantial main sections with 2-3 paragraphs
each, exactly 2 audience-fit paragraphs, exactly 2 conclusion paragraphs, and 3 concise FAQs. Vary
paragraph length and avoid repeating the same advice.

Never use hype, fake testing, generic filler, or any of these phrases:
"in today's fast-paced world", "game-changer", "look no further", "in conclusion",
"it's important to note", "when it comes to", "dive into", "delve into".

JSON schema:
{
  "title": "exact required title",
  "meta_description": "145-160 characters",
  "intro": ["paragraph 1", "paragraph 2", "paragraph 3"],
  "sections": [
    {"heading": "section heading", "paragraphs": ["paragraph", "paragraph"]}
  ],
  "audience_fit": ["paragraph 1", "paragraph 2"],
  "conclusion": ["paragraph 1", "paragraph 2"],
  "faq": [
    {"q": "question", "a": "answer"}
  ]
}"""
    user = f"""REQUIRED TITLE (use verbatim): {topic['title']}
PRIMARY SEARCH PHRASE: {topic['keyword']}
SECTION / NICHE: {topic['niche']}
EDITORIAL BRIEF: {topic['editorial_brief']}

Write a practical guide for readers researching this question. Do not turn it into a product roundup.
No named products and no purchase links. Where equipment is discussed, compare general setup types and
explain what readers should verify before relying on a particular item. Do not invent numbers or standards.
Return the complete JSON object now."""
    return system, user


def _generate_article(topic: dict, cfg: dict) -> tuple[dict, str]:
    editorial_cfg = copy.deepcopy(cfg)
    editorial_cfg.setdefault("content", {})["min_words"] = MIN_EDITORIAL_WORDS
    editorial_cfg["content"]["target_words"] = 850
    ai_cfg = editorial_cfg.setdefault("ai", {})
    ai_cfg["max_output_tokens"] = max(8000, int(ai_cfg.get("max_output_tokens", 6000)))

    system, user = _prompts(topic)
    providers = available_providers(ai_cfg)
    if not providers:
        raise AIError("No configured AI provider key is available for editorial drafting.")

    attempted: set[str] = set()
    last_problems: list[str] = []
    for pass_number in range(1, len(providers) + 1):
        raw, provider, used = generate_json(
            ai_cfg, system, user,
            timeout_note=f"(editorial draft {pass_number}/{len(providers)})",
            exclude_providers=attempted,
            return_attempted=True,
        )
        attempted.update(used)
        article = normalise(raw, topic)
        ok, problems = validate(article, editorial_cfg)
        if ok:
            body = render_html(article)
            rendered_words = word_count(body)
            if rendered_words < MIN_EDITORIAL_WORDS:
                problems = [f"rendered body too short: {rendered_words} words"]
                ok = False
            if re.search(r"<a\b|(?:https?://|www\.)", body, re.I):
                problems.append("links or URLs are prohibited in editorial drafts")
                ok = False
            if re.search(r"\b(?:Amazon|ASIN|in stock|out of stock|available now)\b", body, re.I):
                problems.append("marketplace or inventory language is prohibited in editorial drafts")
                ok = False
        if ok:
            article["_source"] = f"ai:{provider}"
            article["_provider"] = provider
            return article, provider

        last_problems = problems
        log.warning("  editorial output rejected by %s: %s", provider, "; ".join(problems[:5]))
        user += "\n\nYour previous output was rejected for: " + "; ".join(problems[:8])
        user += "\nCorrect all issues and return the complete JSON object only."
    raise AIError("Editorial article failed validation: " + "; ".join(last_problems[:8]))


def render_html(article: dict) -> str:
    parts = [
        '<div class="post-body" style="font-family:Arial,sans-serif;line-height:1.75;color:#202124;font-size:17px;">'
    ]
    for paragraph in article.get("intro", [])[:3]:
        parts.append(f'<p style="margin:0 0 18px;">{escape(str(paragraph))}</p>')
    for section in article.get("sections", []):
        heading = str(section.get("heading") or "").strip()
        if not heading:
            continue
        parts.append(
            '<h2 style="background:#f1f3f4;padding:10px 15px;border-left:5px solid #2e7d32;'
            'font-size:20px;color:#202124;margin:30px 0 15px;">'
            + escape(heading) + "</h2>"
        )
        for paragraph in section.get("paragraphs", []):
            parts.append(f'<p style="margin:0 0 18px;">{escape(str(paragraph))}</p>')
        for bullet in section.get("bullets", []):
            parts.append(f'<p style="margin:0 0 12px;">{escape(str(bullet))}</p>')
    parts.append(
        '<h2 style="background:#f1f3f4;padding:10px 15px;border-left:5px solid #2e7d32;'
        'font-size:20px;color:#202124;margin:30px 0 15px;">Who May Find This Useful?</h2>'
    )
    for paragraph in article.get("audience_fit", [])[:2]:
        parts.append(f'<p style="margin:0 0 18px;">{escape(str(paragraph))}</p>')
    parts.append(
        '<h2 style="background:#f1f3f4;padding:10px 15px;border-left:5px solid #2e7d32;'
        'font-size:20px;color:#202124;margin:30px 0 15px;">Final Thoughts</h2>'
    )
    for paragraph in article.get("conclusion", [])[:2]:
        parts.append(f'<p style="margin:0 0 18px;">{escape(str(paragraph))}</p>')
    faqs = article.get("faq") or []
    if faqs:
        parts.append(
            '<h2 style="background:#f1f3f4;padding:10px 15px;border-left:5px solid #2e7d32;'
            'font-size:20px;color:#202124;margin:30px 0 15px;">Frequently Asked Questions</h2>'
        )
        for item in faqs[:3]:
            parts.append(f'<h3 style="font-size:18px;">{escape(str(item.get("q") or ""))}</h3>')
            parts.append(f'<p style="margin:0 0 18px;">{escape(str(item.get("a") or ""))}</p>')
    parts.append("</div>")
    return "\n".join(parts)


def render_markdown(article: dict, topic: dict) -> str:
    lines = [
        "# " + article["title"], "",
        "> " + article.get("meta_description", ""), "",
        "**Editorial draft — no product links or inventory claims.**", "",
    ]
    lines.extend(article.get("intro", []))
    lines.append("")
    for section in article.get("sections", []):
        lines.extend(["## " + section["heading"], ""])
        lines.extend(section.get("paragraphs", []))
        lines.append("")
    lines.extend(["## Who May Find This Useful?", ""])
    lines.extend(article.get("audience_fit", []))
    lines.extend(["", "## Final Thoughts", ""])
    lines.extend(article.get("conclusion", []))
    if article.get("faq"):
        lines.extend(["", "## Frequently Asked Questions", ""])
        for item in article["faq"][:3]:
            lines.extend(["**" + item["q"] + "**", "", item["a"], ""])
    return "\n".join(lines).rstrip() + "\n"


def _metadata(article: dict, topic: dict, provider: str, body: str) -> dict:
    return {
        "slug": topic["slug"],
        "title": article["title"],
        "meta_description": article.get("meta_description", ""),
        "template": "editorial",
        "niche": topic.get("niche", "general"),
        "keyword": topic.get("keyword", ""),
        "labels": ["Guides", str(topic.get("niche") or "general").title()],
        "source": f"ai:{provider}",
        "provider": provider,
        "words": word_count(body),
        "items": [],
        "html_file": f"{topic['slug']}.html",
        "layout_version": 0,
        "content_type": "editorial",
        "editorial_only": True,
        "product_links_verified": False,
        "stock_verification_required": False,
        "review_required": True,
        "editorial_review_required": True,
        "draft": True,
        "publishable": False,
    }


def generate_batch(count: int = 2) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    topics = load_topics()
    state = _load_json(STATE_PATH, {"generated_slugs": [], "generated": []})
    chosen = select_topics(topics, state, count)
    if not chosen:
        log.info("No unused editorial topics remain; no files changed.")
        return {"generated": 0, "titles": []}

    cfg = load_config()
    created = []
    for topic in chosen:
        article, provider = _generate_article(topic, cfg)
        body = render_html(article)
        if re.search(r"<a\b|(?:https?://|www\.)", body, re.I):
            raise ValueError(f"Editorial draft {topic['slug']} unexpectedly contains a hyperlink or URL.")
        if re.search(r"\b(?:Amazon|ASIN|in stock|out of stock|available now)\b", body, re.I):
            raise ValueError(f"Editorial draft {topic['slug']} contains marketplace or stock language.")
        if word_count(body) < MIN_EDITORIAL_WORDS:
            raise ValueError(f"Editorial draft {topic['slug']} is below {MIN_EDITORIAL_WORDS} words.")

        meta = _metadata(article, topic, provider, body)
        (OUT_DIR / f"{topic['slug']}.html").write_text(body + "\n", encoding="utf-8")
        (OUT_DIR / f"{topic['slug']}.md").write_text(render_markdown(article, topic), encoding="utf-8")
        _write_json(OUT_DIR / f"{topic['slug']}.json", meta)
        state.setdefault("generated_slugs", []).append(topic["slug"])
        state.setdefault("generated", []).append({
            "slug": topic["slug"], "title": article["title"],
            "provider": provider, "words": meta["words"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        created.append({"slug": topic["slug"], "title": article["title"], "words": meta["words"], "provider": provider})
        log.info("  editorial draft saved: %s (%d words; %s)", topic["slug"], meta["words"], provider)

    _write_json(STATE_PATH, state)
    entries = []
    for path in sorted(OUT_DIR.glob("*.json")):
        if path.name in {"index.json", "state.json"}:
            continue
        entry = _load_json(path, None)
        if entry:
            entries.append(entry)
    _write_json(INDEX_PATH, {
        "type": "editorial-drafts-only",
        "count": len(entries),
        "posts": entries,
        "publishing": "disabled: product-free editorial drafts are not sent to the Blogger affiliate publisher",
    })
    return {"generated": len(created), "titles": created}


def main() -> int:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Generate stock-independent editorial drafts.")
    parser.add_argument("--count", type=int, default=2)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = generate_batch(max(1, min(args.count, MAX_BATCH)))
        log.info("Editorial draft run complete: %d new article(s).", result["generated"])
        return 0
    except Exception as exc:
        log.error("Editorial draft run failed: %s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
