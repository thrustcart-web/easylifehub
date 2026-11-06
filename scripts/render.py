"""Renders an article dict into final Blogger-ready HTML (+ markdown archive).

Blogger strips external stylesheets and most classes survive but CSS files do
not, so every post carries a compact scoped <style> block. Schema.org JSON-LD
is injected inside the post body - Google reads it fine from there.

Outputs per article (all written to content/queue/):
  <slug>.json   structured data  (consumed by the Apps Script publisher)
  <slug>.html   final post body  (fetched over raw.githubusercontent.com)
  <slug>.md     human-readable archive
"""
from __future__ import annotations

import json
import re
from html import unescape, escape as html_escape
from pathlib import Path
from urllib.parse import urlsplit

from affiliate import Affiliate, inline_affiliate_links
from util import QUEUE_DIR, log, slugify, word_count


POST_CSS = """
<style>
.post-body{--ink:#1c1f23;--mut:#5b6472;--line:#e6e8ec;--acc:#146c43;--warn:#b42318;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);line-height:1.72;font-size:17px}
.post-body h2{font-size:1.5rem;line-height:1.28;margin:2.1rem 0 .7rem;font-weight:800;letter-spacing:-.015em}
.post-body h3{font-size:1.15rem;margin:1.6rem 0 .5rem;font-weight:700}
.post-body p{margin:0 0 1.05rem}
.post-body ul{margin:0 0 1.15rem;padding-left:1.25rem}
.post-body li{margin-bottom:.5rem}
.post-body a{color:#0b5cad}
.post-body strong{font-weight:700}
.hero{margin:0 0 1.4rem}
.hero img{display:block;width:100%;height:auto;border-radius:14px}
.hero-svg svg{display:block;width:100%;height:auto;border-radius:14px}
.img-credit{font-size:.72rem;color:var(--mut);margin-top:.4rem;text-align:right}
.toc{background:#f6f8fa;border:1px solid var(--line);border-radius:12px;padding:.9rem 1.1rem;margin:0 0 1.6rem}
.toc-title{font-size:.74rem;letter-spacing:.09em;text-transform:uppercase;color:var(--mut);font-weight:700;margin-bottom:.45rem}
.toc ol{margin:0;padding-left:1.15rem}
.toc li{margin-bottom:.25rem;font-size:.95rem}
.keybox{border-left:4px solid var(--acc);background:#f2fbf6;padding:.85rem 1rem;border-radius:0 10px 10px 0;margin:0 0 1.5rem}
.keybox .kb-title{font-weight:800;font-size:.8rem;letter-spacing:.06em;text-transform:uppercase;color:var(--acc);margin-bottom:.3rem}
.spec-table{width:100%;border-collapse:collapse;font-size:.94rem;margin:0 0 1.5rem}
.spec-table caption{text-align:left;font-weight:700;padding-bottom:.45rem;color:var(--mut);font-size:.8rem;letter-spacing:.06em;text-transform:uppercase}
.spec-table th,.spec-table td{border:1px solid var(--line);padding:.55rem .7rem;text-align:left;vertical-align:top}
.spec-table thead th{background:#f6f8fa;font-weight:700}
.spec-table tbody th{background:#fafbfc;font-weight:600;width:32%}
.table-wrap{overflow-x:auto;margin-bottom:1.5rem}
.pc-grid{display:flex;flex-wrap:wrap;gap:.9rem;margin:0 0 1.6rem}
.pc-col{flex:1 1 240px;border:1px solid var(--line);border-radius:12px;padding:.85rem 1rem}
.pc-col.pros{background:#f2fbf6;border-color:#c8ecd8}
.pc-col.cons{background:#fff6f5;border-color:#f6d7d3}
.pc-title{font-weight:800;font-size:.82rem;letter-spacing:.07em;text-transform:uppercase;margin-bottom:.5rem}
.pc-col.pros .pc-title{color:var(--acc)}
.pc-col.cons .pc-title{color:var(--warn)}
.pc-col ul{margin:0;padding-left:1.05rem;font-size:.95rem}
.product-box{border:1px solid var(--line);border-left:4px solid #ff9900;border-radius:12px;padding:.9rem 1.05rem;margin:0 0 1.5rem;background:#fffdf8}
.pb-head{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem;margin-bottom:.6rem}
.pb-name{font-weight:800;font-size:1.02rem}
.pb-badge{font-size:.74rem;background:#fff3e0;color:#8a5200;border-radius:999px;padding:.15rem .6rem;font-weight:700}
.cta-wrap{margin:1.1rem 0 1.6rem}
.cta-btn{display:inline-flex;align-items:center;gap:.5rem;background:#ff9900;color:#111!important;text-decoration:none;font-weight:800;padding:.72rem 1.25rem;border-radius:10px;font-size:1rem}
.cta-btn:hover{background:#e88b00}
.cta-arrow{font-size:1.05rem}
.cta-note{display:block;color:var(--mut);font-size:.78rem;margin-top:.4rem}
.faq{margin:0 0 1.6rem}
.faq details{border:1px solid var(--line);border-radius:10px;padding:.6rem .9rem;margin-bottom:.55rem;background:#fcfcfd}
.faq summary{font-weight:700;cursor:pointer;font-size:1rem}
.faq details p{margin:.6rem 0 0;font-size:.96rem;color:#333}
.verdict{background:#101418;color:#eef1f4;border-radius:14px;padding:1.1rem 1.2rem;margin:0 0 1.6rem}
.verdict h2{color:#fff;margin-top:0}
.verdict p{color:#dfe4e9}
.disclosure{background:#f6f8fa;border:1px dashed #c9ced6;border-radius:10px;padding:.7rem .9rem;font-size:.85rem;color:#4a525e;margin:0 0 1.6rem}
.foot-note{font-size:.78rem;color:var(--mut);border-top:1px solid var(--line);padding-top:.8rem;margin-top:1.6rem}
@media(max-width:600px){.post-body{font-size:16px}.post-body h2{font-size:1.3rem}}
</style>
"""



def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _anchor(heading: str, idx: int) -> str:
    return f"sec-{idx}-{slugify(heading, 40)}"


def _keep_amazon_links_only(html: str) -> str:
    """Keep only links hosted on Amazon.com; unwrap all other anchors."""
    anchor_re = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
    href_re = re.compile(
        r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
        re.IGNORECASE | re.DOTALL,
    )

    def replace_anchor(match: re.Match[str]) -> str:
        href_match = href_re.search(match.group(1) or "")
        if not href_match:
            return match.group(2) or ""
        href = unescape(
            next((part for part in href_match.groups() if part is not None), "")
        ).strip()
        try:
            host = (urlsplit(href).hostname or "").lower()
        except ValueError:
            host = ""
        if host == "amazon.com" or host.endswith(".amazon.com"):
            return match.group(0)
        return match.group(2) or ""

    return anchor_re.sub(replace_anchor, str(html or ""))


def _amazon_button(url: str) -> str:
    """The same green Amazon CTA used in every EasyLifeHub article."""
    try:
        host = (urlsplit(unescape(str(url or ""))).hostname or "").lower()
    except ValueError:
        host = ""
    if host != "amazon.com" and not host.endswith(".amazon.com"):
        return ""
    safe_url = html_escape(str(url), quote=True)
    return (
        '<div style="text-align: center; margin: 30px 0;">'
        f'<a href="{safe_url}" target="_blank" rel="nofollow sponsored noopener" '
        'style="display: inline-block; background-color: #2e7d32; color: #ffffff; '
        'padding: 14px 32px; font-size: 16px; font-weight: bold; '
        'text-decoration: none; border-radius: 6px;">View At Amazon</a>'
        '</div>'
    )


def _clean_post_fragment(value: str) -> str:
    """Never allow style/script blocks from model output into Blogger post body."""
    text = str(value or "").strip()
    text = re.sub(r"<(style|script)\b[^>]*>[\s\S]*?</\1\s*>", "", text, flags=re.I)
    return text

def _toc(sections: list[dict]) -> str:
    if len(sections) < 3:
        return ""
    items = "".join(
        f'<li>{_esc(s["heading"])}</li>'
        for s in sections
    )
    return f'<nav class="toc" aria-label="Table of contents"><div class="toc-title">In this guide</div><ol>{items}</ol></nav>'


def _pros_cons(article: dict) -> str:
    pros = "".join(f"<li>{p}</li>" for p in article.get("pros", []))
    cons = "".join(f"<li>{c}</li>" for c in article.get("cons", []))
    if not pros and not cons:
        return ""
    return (
        '<div class="pc-grid">'
        f'<div class="pc-col pros"><div class="pc-title">What works</div><ul>{pros}</ul></div>'
        f'<div class="pc-col cons"><div class="pc-title">What does not</div><ul>{cons}</ul></div>'
        "</div>"
    )


def _comparison_table(rows: list[dict], items: list[dict]) -> str:
    rows = [r for r in rows if r.get("criterion") and (r.get("a") or r.get("b"))]
    if not rows:
        return ""
    a_name = items[0]["name"] if items else "Option A"
    b_name = items[1]["name"] if len(items) > 1 else "Option B"
    body = "".join(
        f'<tr><th scope="row">{_esc(r["criterion"])}</th><td>{r.get("a", "")}</td><td>{r.get("b", "")}</td></tr>'
        for r in rows
    )
    return (
        '<div class="table-wrap"><table class="spec-table"><caption>Head to head</caption>'
        f"<thead><tr><th>Criterion</th><th>{_esc(a_name)}</th><th>{_esc(b_name)}</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _specs_table(article: dict) -> str:
    """The 'at a glance' criteria table built from article['specs'].

    Owner-supplied data is marked with data-owner-data so the quality gate can
    tell a legitimate number in a table from a hallucinated number in prose.
    """
    specs = [s for s in (article.get("specs") or []) if s.get("label")]
    if len(specs) < 2:
        return ""
    rows = "".join(
        f'<tr><th scope="row">{_esc(s["label"])}</th>'
        f'<td data-owner-data="1">{_esc(s.get("value", "") or "-")}</td></tr>'
        for s in specs
    )
    return (
        '<div class="table-wrap"><table class="spec-table">'
        "<caption>At a glance</caption>"
        f'<thead><tr><th>What to look at</th><th>Detail</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def _faq_html(article: dict) -> str:
    faq = article.get("faq") or []
    if not faq:
        return ""
    blocks = "".join(
        f'<details><summary>{_esc(item["q"])}</summary><p>{item["a"]}</p></details>' for item in faq
    )
    return f'<section class="faq"><h2>Questions buyers actually ask</h2>{blocks}</section>'


def _jsonld(article: dict, topic: dict, aff: Affiliate, cfg: dict) -> str:
    blog = cfg.get("blog", {})
    primary = (topic.get("items") or [{}])[0]
    name = primary.get("name") or topic.get("keyword", "")
    url = aff.url_for(primary) if primary else aff.base
    graph = [
        {
            "@type": "Article",
            "headline": article["title"][:110],
            "description": article.get("meta_description", "")[:300],
            "author": {"@type": "Organization", "name": blog.get("name", "Editorial Team")},
            "publisher": {"@type": "Organization", "name": blog.get("name", "Editorial Team")},
            "mainEntityOfPage": blog.get("url", ""),
            "articleSection": topic.get("niche", "general").title(),
            "inLanguage": cfg.get("content", {}).get("language", "en"),
        },
        {
            "@type": "Product",
            "name": name,
            "description": (article.get("intro") or [""])[0][:300],
            "url": url,
        },
        {
            "@type": "ItemList",
            "name": article["title"][:110],
            "itemListElement": [
                {"@type": "ListItem", "position": i, "name": it.get("display_name") or it.get("name", ""), "url": aff.url_for(it)}
                for i, it in enumerate(topic.get("items", [])[:10], start=1)
                if it.get("name")
            ],
        },
    ]
    faq = article.get("faq") or []
    if faq:
        graph.append(
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {"@type": "Question", "name": item["q"], "acceptedAnswer": {"@type": "Answer", "text": re.sub(r"<[^>]+>", " ", item["a"])[:500]}}
                    for item in faq[:8]
                ],
            }
        )
    pros = article.get("pros") or []
    cons = article.get("cons") or []
    if pros or cons:
        graph.append(
            {
                "@type": "Review",
                "itemReviewed": {"@type": "Product", "name": name},
                "positiveNotes": {"@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": i, "name": p} for i, p in enumerate(pros, 1)]},
                "negativeNotes": {"@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": i, "name": c} for i, c in enumerate(cons, 1)]},
                "author": {"@type": "Organization", "name": blog.get("name", "Editorial Team")},
            }
        )
    payload = {"@context": "https://schema.org", "@graph": graph}
    return '<script type="application/ld+json">' + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def render_html(article: dict, topic: dict, cfg: dict, aff: Affiliate, hero: str) -> str:
    """Render every article with the fixed EasyLifeHub green review layout.

    The layout is deliberately independent of topic: only article copy,
    section headings and the affiliate destination change from post to post.
    """
    items = topic.get("items", [])
    if not items:
        raise ValueError("Cannot render an affiliate article without a product item.")

    parts: list[str] = []

    intro = [_clean_post_fragment(p) for p in (article.get("intro") or [])[:3]]
    intro = [p for p in intro if p]
    if len(intro) != 3:
        raise ValueError("The fixed article layout requires exactly three introduction paragraphs.")

    for paragraph in intro:
        parts.append(f"<p>{paragraph}</p>")

    disclosure = (
        "As an Amazon Associate I earn from qualifying purchases. "
        + str(aff.disclosure or "This post contains affiliate links.")
    )
    parts.append(f"<p><em>{html_escape(disclosure)}</em></p>")

    # Every named product gets its own direct, verified Amazon detail-page link.
    product_cards = []
    for item in items:
        product_url = aff.url_for(item)
        parsed_product_url = urlsplit(product_url)
        if (
            parsed_product_url.netloc.lower() != "www.amazon.com"
            or not re.fullmatch(r"/dp/[A-Z0-9]{10}", parsed_product_url.path)
            or ("tag=" + aff.tag) not in parsed_product_url.query
        ):
            raise ValueError(f"Product has no verified Amazon detail-page URL: {item.get('name', '')}")
        product_cards.append(aff.product_box(item, label="View Product on Amazon"))
    parts.append(
        '<section class="product-links">'
        '<h2 style="background-color:#f1f3f4;padding:10px 15px;border-left:5px solid #2e7d32;'
        'font-size:20px;color:#202124;margin-top:30px;margin-bottom:15px;">Products in This Guide</h2>'
        + "".join(product_cards) + "</section>"
    )

    for section in article.get("sections", []):
        heading = str(section.get("heading") or "").strip()
        if not heading:
            continue
        # The final audience and verdict sections use fixed headings below.
        if re.search(r"\b(who should buy|who may find|who might|final thoughts|the verdict|bottom line|so which one should you get|conclusion)\b", heading, re.I):
            continue

        parts.append(
            '<h2 style="background-color: #f1f3f4; padding: 10px 15px; '
            'border-left: 5px solid #2e7d32; font-size: 20px; color: #202124; '
            'margin-top: 30px; margin-bottom: 15px;">'
            f'{html_escape(heading)}</h2>'
        )

        for paragraph in section.get("paragraphs", []):
            fragment = _clean_post_fragment(paragraph)
            if fragment:
                parts.append(f"<p>{fragment}</p>")

        # Keep the same paragraph-led design even when the source writer
        # returned concise bullet points instead of paragraphs.
        for bullet in section.get("bullets", []):
            fragment = _clean_post_fragment(bullet)
            if fragment:
                parts.append(f"<p>{fragment}</p>")

    parts.append(
        '<h2 style="background-color: #f1f3f4; padding: 10px 15px; '
        'border-left: 5px solid #2e7d32; font-size: 20px; color: #202124; '
        'margin-top: 30px; margin-bottom: 15px;">Who May Find It Useful?</h2>'
    )
    audience_fit = [_clean_post_fragment(p) for p in (article.get("audience_fit") or [])]
    audience_fit = [p for p in audience_fit if p]
    if not audience_fit:
        audience_fit = [
            "This product may suit buyers whose needs match the use cases and features described above.",
            "Before choosing it, compare those priorities with the way you plan to use the product.",
        ]
    for paragraph in audience_fit[:2]:
        parts.append(f"<p>{paragraph}</p>")

    parts.append(
        '<h2 style="background-color: #f1f3f4; padding: 10px 15px; '
        'border-left: 5px solid #2e7d32; font-size: 20px; color: #202124; '
        'margin-top: 30px; margin-bottom: 15px;">Final Thoughts</h2>'
    )
    conclusion = [_clean_post_fragment(p) for p in (article.get("conclusion") or [])]
    conclusion = [p for p in conclusion if p]
    if not conclusion:
        raise ValueError("The fixed article layout requires Final Thoughts copy.")
    for paragraph in conclusion[:2]:
        parts.append(f"<p>{paragraph}</p>")

    # No <style>, <script>, JSON-LD block, TOC, internal links, related links,
    # product cards, tables or auto-generated image banner in the post body.
    return _keep_amazon_links_only("\n".join(parts))
def render_markdown(article: dict, topic: dict, aff: Affiliate) -> str:
    """Human-readable archive copy (also what you would review if you wanted to)."""
    lines = [
        f"# {article['title']}",
        "",
        f"> {article.get('meta_description', '')}",
        "",
        f"*Template: {topic.get('template')} | Niche: {topic.get('niche')} | Source: {article.get('_source')} | Provider: {article.get('_provider', 'n/a')}*",
        "",
    ]
    for para in article.get("intro", []):
        lines += [para, ""]
    for section in article.get("sections", []):
        lines += [f"## {section['heading']}", ""]
        for para in section.get("paragraphs", []):
            lines += [para, ""]
        for bullet in section.get("bullets", []):
            lines.append(f"- {bullet}")
        if section.get("bullets"):
            lines.append("")
    if article.get("pros"):
        lines += ["## Pros", ""] + [f"- {p}" for p in article["pros"]] + [""]
    if article.get("cons"):
        lines += ["## Cons", ""] + [f"- {c}" for c in article["cons"]] + [""]
    if article.get("faq"):
        lines += ["## FAQ", ""]
        for item in article["faq"]:
            lines += [f"**{item['q']}**", "", item["a"], ""]
    lines += ["## Conclusion", ""] + list(article.get("conclusion", [])) + [""]
    lines += ["---", "", "### Product Links", ""]
    for item in topic.get("items", []):
        lines.append(f"- {item.get('display_name') or item.get('name')}: {aff.url_for(item)}")
    lines += ["", aff.disclosure, ""]
    return "\n".join(lines)


def save_article(article: dict, topic: dict, cfg: dict, aff: Affiliate, hero: str) -> Path:
    slug = topic["slug"]
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    html = render_html(article, topic, cfg, aff, hero)
    (QUEUE_DIR / f"{slug}.html").write_text(html, encoding="utf-8")
    (QUEUE_DIR / f"{slug}.md").write_text(render_markdown(article, topic, aff), encoding="utf-8")

    meta = {
        "slug": slug,
        "title": article["title"],
        "meta_description": article.get("meta_description", ""),
        "template": topic.get("template"),
        "niche": topic.get("niche"),
        "keyword": topic.get("keyword"),
        "labels": [cfg.get("content", {}).get("label", "Reviews"), (topic.get("niche") or "general").title()],
        "source": article.get("_source"),
        "provider": article.get("_provider", ""),
        "words": word_count(html),
        "items": [
            {
            "name": i.get("display_name") or i.get("name"),
            "asin": i.get("asin", ""),
            "search_term": i.get("search_term", ""),
            "url": aff.url_for(i),
        }
            for i in topic.get("items", [])
        ],
        "html_file": f"{slug}.html",
        "layout_version": 3,
        "draft": False,
        # Offline fallback copy is useful as a starting point, but it has not
        # been independently fact-checked; keep it out of automatic publishing.
        "product_links_verified": bool(topic.get("items")) and all(
            bool(i.get("asin_verified")) and bool(aff.url_for(i)) for i in topic.get("items", [])
        ),
        "review_required": not (
            (str(article.get("_source") or "").lower().startswith("ai:") or str(article.get("_source") or "").lower() == "seed")
            and bool(topic.get("items"))
            and all(bool(i.get("asin_verified")) and bool(aff.url_for(i)) for i in topic.get("items", []))
        ),
    }
    (QUEUE_DIR / f"{slug}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    rebuild_index(cfg)
    log.info("  queued: %s (%d words, %s)", slug, meta["words"], meta["source"])
    return QUEUE_DIR / f"{slug}.html"


def rebuild_index(cfg: dict) -> None:
    """index.json = the manifest the Apps Script publisher reads. Newest last."""
    entries = []
    for path in sorted(QUEUE_DIR.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            log.warning("skipping malformed queue entry %s", path.name)
    entries.sort(key=lambda e: e.get("slug", ""))
    index = {
        "repo_raw_base": cfg.get("github", {}).get("repo_raw_base", ""),
        "count": len(entries),
        "posts": entries,
    }
    (QUEUE_DIR / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def prune_queue(max_size: int) -> int:
    """Keep the queue bounded so the repo does not grow forever."""
    if max_size <= 0:
        return 0
    files = sorted(QUEUE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    files = [f for f in files if f.name != "index.json"]
    removed = 0
    while len(files) > max_size:
        victim = files.pop(0)
        stem = victim.stem
        for ext in (".json", ".html", ".md"):
            p = QUEUE_DIR / f"{stem}{ext}"
            if p.exists():
                p.unlink()
                removed += 1
    return removed
