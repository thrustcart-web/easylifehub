"""Rebuild eligible legacy queue articles as current V4 without product discovery.

This workflow reuses only already verified ASINs and direct Amazon URLs from
the queue. It never calls Apify, SerpApi, or any product-search endpoint.
The AI writer uses the configured provider fallback chain and the current
quality gate before an article replaces its existing queue files.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from affiliate import Affiliate
from ai_writer import AIError, write_article
from product_resolver import _trusted_item
from render import rebuild_index, render_html, save_article
from safety import quality_gate
from util import QUEUE_DIR, load_config, setup_logging, log


def is_verified_item(item: dict, tag: str) -> bool:
    """Accept only an existing verified ASIN and its exact tagged /dp/ URL."""
    if not isinstance(item, dict) or not _trusted_item(item):
        return False
    asin = str(item.get("asin") or "").strip().upper()
    url = str(item.get("url") or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    tags = parse_qs(parsed.query).get("tag", [])
    return (
        bool(re.fullmatch(r"[A-Z0-9]{10}", asin))
        and parsed.scheme == "https"
        and parsed.hostname == "www.amazon.com"
        and parsed.path == f"/dp/{asin}"
        and tags == [tag]
        and not parsed.fragment
    )


def is_rebuild_candidate(meta: dict, tag: str) -> bool:
    """Only rebuild old AI articles that already passed editorial and link gates."""
    if not isinstance(meta, dict):
        return False
    if int(meta.get("layout_version") or 0) >= 4:
        return False
    if meta.get("review_required") is not False:
        return False
    if meta.get("product_links_verified") is not True:
        return False
    if not str(meta.get("source") or "").startswith("ai:"):
        return False
    items = meta.get("items") or []
    return bool(items) and all(is_verified_item(item, tag) for item in items)


def topic_from_meta(meta: dict) -> dict:
    """Build a writer topic from the exact queue metadata, without discovery."""
    items = []
    for raw in meta["items"]:
        item = dict(raw)
        item["display_name"] = str(item.get("name") or "").strip()
        item["name"] = item["display_name"]
        items.append(item)
    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "meta_description": meta.get("meta_description", ""),
        "template": meta.get("template", "review"),
        "niche": meta.get("niche", "general"),
        "keyword": meta.get("keyword") or meta["title"],
        "labels": list(meta.get("labels") or []),
        "items": items,
    }


def rebuild(limit: int = 8) -> dict:
    cfg = load_config()
    aff = Affiliate(cfg)
    index_path = QUEUE_DIR / "index.json"
    if not index_path.exists():
        raise SystemExit("Queue index not found; nothing was changed.")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    candidates = []
    for listed in index.get("posts", []):
        slug = str(listed.get("slug") or "").strip()
        if not slug:
            continue
        meta_path = QUEUE_DIR / f"{slug}.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if is_rebuild_candidate(meta, aff.tag):
            candidates.append(meta)

    candidates = sorted(candidates, key=lambda row: str(row.get("slug")))[:max(1, min(int(limit), 8))]
    if not candidates:
        raise SystemExit("No eligible verified legacy AI articles were found.")

    results = []
    for meta in candidates:
        slug = meta["slug"]
        try:
            topic = topic_from_meta(meta)
            article, provider = write_article(topic, cfg)
            article["_source"] = f"ai:{provider}"
            article["_provider"] = provider

            # render_html inserts only the two required green Amazon CTA buttons
            # per verified item. It does not fetch images or product data.
            html = render_html(article, topic, cfg, aff, hero="")
            ok, reasons = quality_gate(
                html, article, cfg, expected_product_count=len(topic["items"])
            )
            if not ok:
                raise AIError("quality gate: " + "; ".join(reasons[:6]))

            save_article(article, topic, cfg, aff, hero="")
            saved = json.loads((QUEUE_DIR / f"{slug}.json").read_text(encoding="utf-8"))
            if (
                int(saved.get("layout_version") or 0) != 4
                or saved.get("review_required") is not False
                or saved.get("product_links_verified") is not True
            ):
                raise AIError("post-save queue metadata did not pass the V4 publication checks")

            results.append({
                "slug": slug,
                "title": saved.get("title", ""),
                "provider": provider,
                "words": saved.get("words", 0),
                "status": "rebuilt_v4",
            })
            log.info("V4 REBUILT | %s | %s | %s words", slug, provider, saved.get("words", 0))
        except Exception as exc:  # one model or article failure must not discard the other upgrades
            results.append({"slug": slug, "status": "skipped", "reason": str(exc)[:240]})
            log.warning("V4 REBUILD SKIPPED | %s | %s", slug, str(exc)[:240])

    rebuild_index(cfg)
    summary = {
        "eligible": len(candidates),
        "rebuilt": sum(1 for row in results if row["status"] == "rebuilt_v4"),
        "skipped": sum(1 for row in results if row["status"] == "skipped"),
        "product_discovery_requests": 0,
        "blogger_posts_published": 0,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["rebuilt"] == 0:
        raise SystemExit("No articles passed the current AI and V4 quality gates.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=8, help="maximum legacy articles to rebuild (hard cap: 8)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)
    rebuild(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
