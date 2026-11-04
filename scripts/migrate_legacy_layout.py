#!/usr/bin/env python3
"""One-time migration of legacy queue entries to the EasyLifeHub v3 post layout.
This migration is idempotent and leaves every legacy post marked for review.

The migration changes presentation, not claims. Legacy posts are marked
review_required=true because the old copy may contain unsupported product facts.
"""
from __future__ import annotations

import ast
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "content" / "queue"
H2_STYLE = (
    "background-color: #f1f3f4; padding: 10px 15px; "
    "border-left: 5px solid #2e7d32; font-size: 20px; color: #202124; "
    "margin-top: 30px; margin-bottom: 15px;"
)


def safe_inline(value: str) -> str:
    text = html.unescape(str(value or "")).strip()
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    tags: list[str] = []

    def hold(match: re.Match[str]) -> str:
        tag = match.group(0).lower()
        token = f"@@SAFE_TAG_{len(tags)}@@"
        tags.append(tag)
        return token

    text = re.sub(r"</?(?:strong|em)\s*>", hold, text, flags=re.I)
    text = re.sub(r"<[^>]*>", "", text)
    text = html.escape(text, quote=False)
    for index, tag in enumerate(tags):
        text = text.replace(f"@@SAFE_TAG_{index}@@", tag)
    return text.strip()


def parse_archive(source: str) -> tuple[list[str], list[dict]]:
    intro: list[str] = []
    sections: list[dict] = []
    current: dict | None = None
    buffer: list[str] = []
    footer = False

    def add(text: str) -> None:
        text = text.strip()
        if not text:
            return
        if current is None:
            intro.append(text)
        else:
            current["paragraphs"].append(text)

    def flush() -> None:
        nonlocal buffer
        value = re.sub(r"\s+", " ", " ".join(buffer)).strip()
        buffer = []
        if value:
            add(value)

    for raw in source.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line == "---":
            flush()
            footer = True
            continue
        if footer:
            continue
        if line.startswith("# ") or line.startswith("> ") or line.startswith("*Template:"):
            continue

        heading = re.match(r"^##\s+(.+)$", line)
        if heading:
            flush()
            title = heading.group(1).strip()
            if title.lower() in {"category", "links"}:
                footer = True
                current = None
                continue
            current = {"heading": title, "paragraphs": []}
            sections.append(current)
            continue

        if re.match(r"^[-*]\s+", line):
            flush()
            add(re.sub(r"^[-*]\s+", "", line).strip())
            continue

        # Recover list values that older archives accidentally wrote as Python repr.
        if line.startswith("[") and line.endswith("]") and "<strong>" in line.lower():
            flush()
            try:
                values = ast.literal_eval(line)
                if isinstance(values, list):
                    for value in values:
                        add(str(value))
                    continue
            except (ValueError, SyntaxError):
                pass

        if line.lower() in {"bullets", "bullets:", ":"}:
            continue
        buffer.append(line)

    flush()
    return intro, sections


def split_paragraph(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.findall(r"[^.!?]+[.!?]+(?:[\"'’”)]*)|[^.!?]+$", text)
    sentences = [part.strip() for part in sentences if part.strip()]
    if len(sentences) >= 2:
        midpoint = max(1, len(sentences) // 2)
        return [" ".join(sentences[:midpoint]), " ".join(sentences[midpoint:])]
    words = text.split()
    if len(words) >= 36:
        midpoint = len(words) // 2
        return [" ".join(words[:midpoint]), " ".join(words[midpoint:])]
    return [text]


def ensure_three_intro(intro: list[str], sections: list[dict]) -> list[str]:
    intro = [x for x in intro if x.strip()]
    while len(intro) < 3:
        for i, paragraph in enumerate(intro):
            parts = split_paragraph(paragraph)
            if len(parts) == 2:
                intro[i:i + 1] = parts
                break
        else:
            if sections and sections[0]["paragraphs"]:
                intro.append(sections[0]["paragraphs"].pop(0))
            else:
                intro.append("This guide explains the main points to compare before choosing, using practical criteria rather than unsupported product claims.")
    if len(intro) > 3:
        intro = intro[:2] + [" ".join(intro[2:])]
    return [safe_inline(x) for x in intro[:3]]


def migrate(meta: dict) -> tuple[str, int]:
    slug = meta.get("slug", "")
    md_path = QUEUE / f"{slug}.md"
    if not md_path.exists():
        raise FileNotFoundError(f"Markdown archive missing for {slug}")
    intro_raw, sections = parse_archive(md_path.read_text(encoding="utf-8"))
    intro = ensure_three_intro(intro_raw, sections)

    audience = []
    conclusion = []
    body = []
    for section in sections:
        heading = section["heading"].strip()
        lower = heading.lower()
        paragraphs = [p for p in section["paragraphs"] if p.strip()]
        if lower in {"pros", "cons", "faq", "frequently asked questions", "questions buyers actually ask"}:
            continue
        if re.search(r"^(who\b|who may|who might|buyer profiles|target audience|who should)", lower):
            audience = paragraphs[:2]
            continue
        if re.search(r"^(conclusion|final thoughts|verdict|final recommendation|so which)", lower):
            conclusion = paragraphs[:2]
            continue
        if paragraphs:
            body.append({"heading": heading, "paragraphs": paragraphs})

    # If the archive had no explicit audience section, derive it from buyer guidance.
    if not audience:
        candidate = next((s for s in body if re.search(r"choose|buyer|useful|fit|deciding|check before", s["heading"], re.I)), None)
        if candidate:
            audience = candidate["paragraphs"][:2]
    if not audience:
        names = [item.get("name", "the products") for item in meta.get("items", [])[:2]]
        subject = " and ".join(names) if names else "the options in this guide"
        audience = [
            f"This guide is most useful to readers comparing {safe_inline(subject)} and deciding which features matter for their own use.",
            "It is especially relevant when you have a clear use case and can compare compatibility, dimensions, maintenance and ownership details before ordering."
        ]
    if len(audience) == 1:
        parts = split_paragraph(audience[0])
        audience = parts if len(parts) == 2 else [audience[0], "The right fit depends on the use case, available space and the features that matter most to the buyer."]
    audience = [safe_inline(x) for x in audience[:2]]

    if not conclusion:
        last = body[-1]["paragraphs"] if body else []
        conclusion = last[-2:] if len(last) >= 2 else []
    if not conclusion:
        conclusion = [
            "The most sensible choice is the one whose documented features match the way it will be used, rather than the option with the broadest marketing claims.",
            "Before buying, confirm the current specifications, compatibility and included items on the manufacturer's product page or the retailer listing."
        ]
    elif len(conclusion) == 1:
        parts = split_paragraph(conclusion[0])
        conclusion = parts if len(parts) == 2 else [conclusion[0], "Compare the confirmed details with your own requirements before making the final choice."]
    conclusion = [safe_inline(x) for x in conclusion[:2]]

    if len(body) < 3:
        raise ValueError(f"{slug}: fewer than three usable body sections after cleanup")

    items = meta.get("items") or []
    amazon_url = next((x.get("url", "") for x in items if re.match(r"^https?://(?:[^/]+\.)?amazon\.com/", x.get("url", ""), re.I)), "")
    cta = ""
    if amazon_url:
        safe_url = html.escape(amazon_url, quote=True)
        cta = (
            '<div style="text-align: center; margin: 30px 0;">'
            f'<a href="{safe_url}" target="_blank" rel="nofollow sponsored noopener" '
            'style="display: inline-block; background-color: #2e7d32; color: #ffffff; '
            'padding: 14px 32px; font-size: 16px; font-weight: bold; '
            'text-decoration: none; border-radius: 6px;">View At Amazon</a></div>'
        )

    parts = [f"<p>{p}</p>" for p in intro]
    parts.append(
        "<p><em>As an Amazon Associate I earn from qualifying purchases. "
        "This post contains affiliate links; we may earn a commission when you buy through them.</em></p>"
    )
    if cta:
        parts.append(cta)

    for section in body:
        heading = safe_inline(section["heading"])
        parts.append(f'<h2 style="{H2_STYLE}">{heading}</h2>')
        parts.extend(f"<p>{safe_inline(p)}</p>" for p in section["paragraphs"])

    parts.append(f'<h2 style="{H2_STYLE}">Who May Find It Useful?</h2>')
    parts.extend(f"<p>{p}</p>" for p in audience)
    if cta:
        parts.append(cta)
    parts.append(f'<h2 style="{H2_STYLE}">Final Thoughts</h2>')
    parts.extend(f"<p>{p}</p>" for p in conclusion)

    html_body = "\n".join(parts).strip()
    if "<style" in html_body.lower() or "<script" in html_body.lower():
        raise ValueError(f"{slug}: unsafe style/script leaked into post")
    links = re.findall(r"<a\b[^>]*href=[\"']([^\"']+)", html_body, re.I)
    if any("amazon.com" not in url.lower() for url in links):
        raise ValueError(f"{slug}: non-Amazon link survived migration")
    words = len(re.findall(r"[\w’'-]+", re.sub(r"<[^>]+>", " ", html_body)))
    meta["layout_version"] = 3
    meta["words"] = words
    meta["review_required"] = True
    meta["migration_status"] = "layout_v3_needs_fact_check"
    (QUEUE / f"{slug}.html").write_text(html_body, encoding="utf-8")
    (QUEUE / f"{slug}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return slug, words


def main() -> None:
    index_path = QUEUE / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    posts = index.get("posts", [])
    changed = []
    for meta in posts:
        if int(meta.get("layout_version", 0) or 0) >= 3:
            continue
        slug, words = migrate(meta)
        changed.append({"slug": slug, "words": words})
    index["posts"] = posts
    index["count"] = len(posts)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"migrated": len(changed), "posts": changed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
