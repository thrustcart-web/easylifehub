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
from pathlib import Path

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


def _toc(sections: list[dict]) -> str:
    if len(sections) < 3:
        return ""
    items = "".join(
        f'<li><a href="#{_anchor(s["heading"], i)}">{_esc(s["heading"])}</a></li>'
        for i, s in enumerate(sections, start=1)
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
    items = topic.get("items", [])
    parts: list[str] = [POST_CSS, hero, aff.disclosure_block(), _jsonld(article, topic, aff, cfg)]

    intro = article.get("intro") or []
    if intro:
        parts.append(f'<div class="keybox"><div class="kb-title">The short version</div><p>{intro[0]}</p></div>')
        for para in intro[1:]:
            parts.append(f"<p>{para}</p>")

    parts.append(_toc(article.get("sections", [])))

    if items:
        parts.append(aff.product_box(items[0], "Check the live price on Amazon"))

    for idx, section in enumerate(article.get("sections", []), start=1):
        parts.append(f'<h2 id="{_anchor(section["heading"], idx)}">{_esc(section["heading"])}</h2>')
        for para in section.get("paragraphs", []):
            if para:
                parts.append(f"<p>{para}</p>")
        bullets = section.get("bullets") or []
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
        # a CTA every ~3 sections keeps conversion without looking spammy
        if idx % 3 == 0 and items:
            parts.append(aff.cta_button(items[0].get("asin", ""), items[0].get("search_term") or items[0].get("name", "")))

    cmp_rows = article.get("comparison_rows") or []
    if cmp_rows:
        parts.append(_comparison_table(cmp_rows, items))

    specs_html = _specs_table(article)
    if specs_html:
        parts.append(specs_html)

    if len(items) > 1:
        parts.append('<h2>Every option in this guide</h2>')
        for item in items[:6]:
            parts.append(aff.product_box(item))

    parts.append(_pros_cons(article))
    parts.append(_faq_html(article))

    conclusion = article.get("conclusion") or []
    if conclusion:
        parts.append('<div class="verdict"><h2>The bottom line</h2>')
        parts.extend(f"<p>{p}</p>" for p in conclusion)
        parts.append("</div>")

    if items:
        parts.append(aff.cta_button(items[0].get("asin", ""), items[0].get("search_term") or items[0].get("name", ""), "See the current Amazon price"))

    parts.append(aff.footer_note())

    html = "\n".join(parts)
    html = inline_affiliate_links(html, aff, items)
    return html


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
    lines += ["---", "", "### Links", ""]
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
        "draft": False,
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
