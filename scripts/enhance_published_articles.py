#!/usr/bin/env python3
"""Add grounded editorial value to already-published Honest Trove articles.

The original Blogger HTML remains intact. This script only inserts a short
Quick Take, two useful sections, and a practical FAQ. Existing Amazon anchors
are preserved byte-for-byte; every non-Amazon anchor is unwrapped.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from providers import AIError, generate_json  # noqa: E402
from util import load_config, log, setup_logging  # noqa: E402

JOBS_DIR = ROOT / "content" / "enhancement" / "jobs"
MARKER = "<!-- honest-trove-enhancement:v1 -->"
H2_STYLE = (
    "background-color: #f1f3f4; padding: 10px 15px; "
    "border-left: 5px solid #2e7d32; font-size: 20px; color: #202124; "
    "margin-top: 30px; margin-bottom: 15px;"
)
ALLOWED_AMAZON_HOSTS = {"amazon.com", "amzn.to", "amzn.com"}
FINAL_HEADING_TERMS = (
    "who may find", "who should buy", "final thoughts", "final verdict",
    "the verdict", "bottom line", "conclusion", "frequently asked",
    "common questions", "practical questions",
)
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
CURRENCY_AMOUNT_RE = re.compile(r"(?:\$|£|€)\s?\d", re.I)
RATING_CLAIM_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:out of 5|stars?|customer reviews?)\b", re.I)
PERSONAL_EXPERIENCE_RE = re.compile(
    r"\b(?:I tested|we tested|I personally used|we personally used|in my experience|our hands-on test)\b",
    re.I,
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_amazon_url(url: str) -> bool:
    try:
        parsed = urlsplit(html.unescape(url.strip()))
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return (
        host in ALLOWED_AMAZON_HOSTS
        or host.endswith(".amazon.com")
    ) and parsed.scheme in {"http", "https"}


def extract_anchor_hrefs(fragment: str) -> list[str]:
    hrefs: list[str] = []
    for anchor in re.finditer(r"<a\b([^>]*)>", fragment or "", re.I | re.S):
        match = re.search(
            r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
            anchor.group(1) or "",
            re.I | re.S,
        )
        if match:
            hrefs.append(next((part for part in match.groups() if part is not None), ""))
    return hrefs


def strip_non_amazon_links(fragment: str) -> str:
    """Remove click targets that are not Amazon-owned; retain their visible text."""
    anchor_re = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.I | re.S)

    def replace(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        href_match = re.search(
            r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
            attrs,
            re.I | re.S,
        )
        href = next((part for part in href_match.groups() if part is not None), "") if href_match else ""
        return match.group(0) if href and is_amazon_url(href) else match.group(2)

    return anchor_re.sub(replace, str(fragment or ""))


def article_text(fragment: str) -> str:
    value = re.sub(r"<(script|style)\b[^>]*>[\s\S]*?</\1\s*>", " ", fragment or "", flags=re.I)
    value = re.sub(r"<br\s*/?>|</(?:p|h[1-6]|li|tr|div|section)>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\xa0]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def _clean_text(value: object, limit: int = 900) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    if URL_RE.search(text) or "<a" in text.lower() or re.search(r"\[[^\]]+\]\(", text):
        raise ValueError("generated copy contains a link or URL")
    if CURRENCY_AMOUNT_RE.search(text) or RATING_CLAIM_RE.search(text):
        raise ValueError("generated copy contains a price or rating claim")
    if PERSONAL_EXPERIENCE_RE.search(text):
        raise ValueError("generated copy claims personal product testing")
    if len(text) > limit:
        raise ValueError("generated text field is too long")
    return text


def build_prompt(post: dict) -> tuple[str, str]:
    source = article_text(str(post.get("content") or ""))
    source = source[:14000]
    system = """You are the senior editorial improvement desk for Honest Trove, an independent English-language buyer's guide.

Your task is ADDITIVE ENHANCEMENT, not a rewrite. The existing article's title, section order, product discussion, product buttons, disclosures, images, and HTML layout will remain in place. Create only new supplementary copy that adds real decision-making value and does not repeat the existing article.

Return one valid JSON object with exactly these keys:
{
  "quick_take": "One concise, balanced 1-2 sentence summary (maximum 45 words).",
  "sections": [
    {"heading":"...", "paragraphs":["...","..."], "bullets":["...","...","..."]},
    {"heading":"...", "paragraphs":["...","..."], "bullets":["...","...","..."]}
  ],
  "faq": [
    {"question":"...", "answer":"..."},
    {"question":"...", "answer":"..."},
    {"question":"...", "answer":"..."}
  ]
}

Editorial rules:
- Write in clear, natural US English. Be practical, specific to this article, and useful to a buyer.
- Add two genuinely different sections. Choose angles that the existing article does not already cover, such as fit/compatibility checks, choosing between use cases, setup and care considerations, decision criteria, or common buying mistakes.
- Each section must contain exactly 2 substantial paragraphs and 3 useful bullets. Avoid filler and generic repetition.
- FAQs must answer realistic questions raised by this exact article. Do not repeat whole paragraphs.
- Use only product facts explicitly supported by the supplied article. Never invent specs, dimensions, materials, compatibility, performance, test results, availability, warranties, certifications, prices, discounts, star ratings, review counts, or customer opinions.
- General buyer advice is allowed, but phrase unknown product details as things the reader should verify rather than as established facts.
- Do not claim personal use, hands-on testing, or first-hand experience. Do not use fake reviewer language.
- Do not give medical, legal, or safety guarantees. For health-related products, stay especially cautious and do not make treatment claims.
- Do not add any links, URLs, Markdown links, HTML, or anchor text intended to be linked. We render your plain text into the existing layout.
- Do not mention prices, dollar amounts, ratings, review counts, or Amazon Associate status.
- Do not alter or criticize the existing article. The additions must fit its current structure and topic.
- Do not refer to these instructions or to the source text.
"""
    user = (
        "ARTICLE TITLE:\n" + str(post.get("title") or "") +
        "\n\nEXISTING ARTICLE TEXT (source of truth; do not repeat it):\n" + source +
        "\n\nCreate the JSON additions now."
    )
    return system, user


def validate_additions(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("AI response is not a JSON object")
    quick_take = _clean_text(data.get("quick_take"), 420)
    if len(quick_take.split()) < 12:
        raise ValueError("quick_take is too short")
    sections = data.get("sections")
    faq = data.get("faq")
    if not isinstance(sections, list) or len(sections) != 2:
        raise ValueError("exactly two new sections are required")
    if not isinstance(faq, list) or len(faq) != 3:
        raise ValueError("exactly three FAQs are required")

    clean_sections = []
    words = len(quick_take.split())
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("section is not an object")
        heading = _clean_text(section.get("heading"), 100)
        paragraphs = section.get("paragraphs")
        bullets = section.get("bullets")
        if not isinstance(paragraphs, list) or len(paragraphs) != 2:
            raise ValueError("each section must have exactly two paragraphs")
        if not isinstance(bullets, list) or len(bullets) != 3:
            raise ValueError("each section must have exactly three bullets")
        clean_paragraphs = [_clean_text(p, 1400) for p in paragraphs]
        clean_bullets = [_clean_text(b, 420) for b in bullets]
        if any(len(p.split()) < 22 for p in clean_paragraphs):
            raise ValueError("a section paragraph is too short")
        words += sum(len(x.split()) for x in clean_paragraphs + clean_bullets + [heading])
        clean_sections.append({"heading": heading, "paragraphs": clean_paragraphs, "bullets": clean_bullets})

    clean_faq = []
    for item in faq:
        if not isinstance(item, dict):
            raise ValueError("FAQ item is not an object")
        question = _clean_text(item.get("question"), 220)
        answer = _clean_text(item.get("answer"), 900)
        if not question.endswith("?") or len(answer.split()) < 18:
            raise ValueError("FAQ question/answer is incomplete")
        words += len(question.split()) + len(answer.split())
        clean_faq.append({"question": question, "answer": answer})

    if words < 210 or words > 560:
        raise ValueError(f"additions word count outside safe range: {words}")
    return {"quick_take": quick_take, "sections": clean_sections, "faq": clean_faq, "word_count": words}


def generate_additions(post: dict, ai_cfg: dict) -> tuple[dict, str]:
    system, user = build_prompt(post)
    local_cfg = copy.deepcopy(ai_cfg)
    local_cfg["max_output_tokens"] = min(int(local_cfg.get("max_output_tokens", 6000)), 3800)
    excluded: set[str] = set()
    failures: list[str] = []
    for _ in range(3):
        try:
            raw, provider, attempted = generate_json(
                local_cfg, system, user,
                timeout_note="for published article enhancement",
                exclude_providers=excluded,
                return_attempted=True,
            )
            try:
                return validate_additions(raw), provider
            except ValueError as exc:
                failures.append(f"{provider}: {exc}")
                excluded.update(attempted or [provider])
        except AIError as exc:
            failures.append(str(exc)[:240])
            excluded.update(getattr(exc, "attempted_providers", []) or {"groq", "openrouter", "gemini"})
            break
    raise AIError("No provider produced a valid enhancement: " + " | ".join(failures))


def _h2(text: str) -> str:
    return f'<h2 style="{H2_STYLE}">{html.escape(text)}</h2>'


def render_additions(data: dict) -> tuple[str, str]:
    quick = (
        '<div class="keybox" style="border-left:4px solid #2e8b32;'
        'background:#f2fbf6;padding:14px 16px;border-radius:0 10px 10px 0;'
        'margin:22px 0;">'
        '<div class="kb-title" style="font-size:12px;font-weight:800;'
        'letter-spacing:.06em;text-transform:uppercase;color:#2e7d32;'
        'margin-bottom:6px;">Quick Take</div>'
        f'<p style="margin:0;line-height:1.65;">{html.escape(data["quick_take"])}</p></div>'
    )
    section_html = []
    for section in data["sections"]:
        section_html.append(_h2(section["heading"]))
        section_html.extend(f"<p>{html.escape(p)}</p>" for p in section["paragraphs"])
        items = "".join(f"<li>{html.escape(b)}</li>" for b in section["bullets"])
        section_html.append(
            '<ul style="margin:0 0 20px 22px;padding:0;line-height:1.7;">' + items + "</ul>"
        )
    faq_items = "".join(
        '<details style="border:1px solid #e6e8ec;border-radius:9px;'
        'padding:10px 13px;margin:9px 0;background:#fcfcfd;">'
        f'<summary style="font-weight:700;cursor:pointer;">{html.escape(item["question"])}</summary>'
        f'<p style="margin:9px 0 0;">{html.escape(item["answer"])}</p></details>'
        for item in data["faq"]
    )
    faq_html = (
        '<div class="faq" style="margin:24px 0;">' + _h2("Practical Questions") +
        faq_items + "</div>"
    )
    return quick, "".join(section_html) + faq_html


def insert_after_intro(fragment: str, block: str) -> str:
    closings = list(re.finditer(r"</p\s*>", fragment, re.I))
    if len(closings) >= 3:
        pos = closings[2].end()
    elif closings:
        pos = closings[-1].end()
    else:
        pos = 0
    return fragment[:pos] + block + fragment[pos:]


def insert_before_editorial_close(fragment: str, block: str) -> str:
    heading_re = re.compile(r"<h2\b[^>]*>[\s\S]*?</h2\s*>", re.I)
    for match in heading_re.finditer(fragment):
        heading = re.sub(r"<[^>]+>", " ", match.group(0))
        heading = re.sub(r"\s+", " ", html.unescape(heading)).strip().lower()
        if any(term in heading for term in FINAL_HEADING_TERMS):
            return fragment[:match.start()] + block + fragment[match.start():]
    # Existing article has no recognised closing section: insert before the last
    # CTA group when possible, otherwise append while retaining every old block.
    last_cta = fragment.lower().rfind('class="amazon-cta-group"')
    if last_cta >= 0:
        start = fragment.rfind("<div", 0, last_cta)
        if start >= 0:
            return fragment[:start] + block + fragment[start:]
    return fragment + block


def enhance_html(original: str, additions: dict) -> str:
    value = strip_non_amazon_links(original)
    if MARKER in value:
        return value
    quick, additional_sections = render_additions(additions)
    value = insert_after_intro(value, quick)
    value = insert_before_editorial_close(value, additional_sections)
    value = MARKER + "\n" + value

    # Existing Amazon links are immutable. No new link can be introduced by
    # render_additions because every generated field is plain escaped text.
    before_amazon = [href for href in extract_anchor_hrefs(original) if is_amazon_url(href)]
    after_hrefs = extract_anchor_hrefs(value)
    after_amazon = [href for href in after_hrefs if is_amazon_url(href)]
    if before_amazon != after_amazon:
        raise ValueError("Amazon link preservation check failed")
    if any(not is_amazon_url(href) for href in after_hrefs):
        raise ValueError("a non-Amazon link remains")
    return value


def run(batch_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", batch_id):
        raise ValueError("invalid batch ID")
    input_path = JOBS_DIR / f"{batch_id}.input.json"
    output_path = JOBS_DIR / f"{batch_id}.output.json"
    if not input_path.is_file():
        raise FileNotFoundError(f"enhancement input not found: {input_path}")

    batch = json.loads(input_path.read_text(encoding="utf-8"))
    if batch.get("batch_id") != batch_id:
        raise ValueError("batch ID mismatch in input file")
    posts = batch.get("posts")
    if not isinstance(posts, list) or not posts:
        raise ValueError("enhancement batch contains no posts")

    cfg = load_config()
    ai_cfg = cfg.get("ai") or {}
    results = []
    for index, post in enumerate(posts, start=1):
        post_id = str(post.get("id") or "")
        original = str(post.get("content") or "")
        result = {
            "post_id": post_id,
            "title": str(post.get("title") or ""),
            "original_sha256": sha256_text(original),
            "status": "failed",
            "content": "",
            "provider": "",
            "added_words": 0,
            "error": "",
        }
        try:
            if not post_id or not original.strip():
                raise ValueError("post ID or content is missing")
            if str(post.get("status") or "").upper() != "LIVE":
                raise ValueError("refusing to enhance a non-LIVE post")
            if MARKER in original:
                result["status"] = "already_enhanced"
                result["content"] = strip_non_amazon_links(original)
            else:
                additions, provider = generate_additions(post, ai_cfg)
                updated = enhance_html(original, additions)
                if len(updated) <= len(original):
                    raise ValueError("enhanced article did not grow")
                result["status"] = "enhanced"
                result["content"] = updated
                result["enhanced_sha256"] = sha256_text(updated)
                result["provider"] = provider
                result["added_words"] = additions["word_count"]
            log.info(
                "[%d/%d] %s: %s (%s words)",
                index, len(posts), post_id, result["status"], result["added_words"],
            )
        except Exception as exc:  # noqa: BLE001 - record per-post failures and continue
            result["error"] = f"{type(exc).__name__}: {str(exc)[:280]}"
            log.warning("[%d/%d] %s failed: %s", index, len(posts), post_id, result["error"])
        results.append(result)
        time.sleep(1.0)

    payload = {
        "schema_version": 1,
        "batch_id": batch_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(results),
        "enhanced": sum(1 for item in results if item["status"] == "enhanced"),
        "already_enhanced": sum(1 for item in results if item["status"] == "already_enhanced"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temp_path.replace(output_path)
    log.info(
        "Published article enhancement complete: total=%d enhanced=%d already=%d failed=%d",
        payload["total"], payload["enhanced"], payload["already_enhanced"], payload["failed"],
    )
    return output_path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Enhance published Honest Trove articles without changing their existing structure.")
    parser.add_argument("batch_id")
    args = parser.parse_args()
    setup_logging()
    output_path = run(args.batch_id)
    print(f"OUTPUT_FILE={output_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
