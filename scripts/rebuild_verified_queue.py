"""Rebuild legacy queue articles as current V4 from existing verified products only.

No Apify, SerpApi, or product discovery endpoint is called. Products are
accepted only when their exact ASIN/title pair is already verified in queue
metadata or can be matched against the local Amazon ASIN cache.
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
from product_resolver import _load_cache, _normal_name, _trusted_item, title_matches
from render import rebuild_index, render_html, save_article
from safety import quality_gate
from util import QUEUE_DIR, load_config, setup_logging, log


def resolve_from_existing_data(item: dict, cache: dict, aff: Affiliate) -> dict | None:
    """Return a verified item using queue data or the local cache; never searches."""
    if not isinstance(item, dict):
        return None
    resolved = dict(item)
    name = str(resolved.get("display_name") or resolved.get("name") or "").strip()
    if not name:
        return None
    resolved["name"] = name
    resolved["display_name"] = name

    if not _trusted_item(resolved):
        cache_keys = [_normal_name(name)]
        search_term = str(resolved.get("search_term") or "").strip()
        if search_term:
            cache_keys.append(_normal_name(search_term))
        cached = next(
            (
                cache.get(key)
                for key in cache_keys
                if isinstance(cache.get(key), dict)
                and re.fullmatch(r"[A-Z0-9]{10}", str(cache[key].get("asin") or "").upper())
                and title_matches(name, str(cache[key].get("title") or ""))
            ),
            None,
        )
        if cached is None:
            return None
        resolved["asin"] = str(cached["asin"]).upper()
        resolved["asin_verified"] = True
        resolved["verified_product_title"] = str(cached["title"])

    if not _trusted_item(resolved):
        return None

    # Rebuild the tagged /dp/ URL from the verified ASIN and configured tag.
    url = aff.url_for(resolved)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    asin = str(resolved.get("asin") or "").upper()
    tags = parse_qs(parsed.query).get("tag", [])
    if not (
        parsed.scheme == "https"
        and parsed.hostname == "www.amazon.com"
        and parsed.path == f"/dp/{asin}"
        and tags == [aff.tag]
        and not parsed.fragment
    ):
        return None
    resolved["url"] = url
    return resolved


def resolved_items_for_meta(meta: dict, cache: dict, aff: Affiliate) -> list[dict] | None:
    """Return all queue products only when every one is verified from local data."""
    items = meta.get("items") or []
    if not items:
        return None
    resolved = [resolve_from_existing_data(item, cache, aff) for item in items]
    if any(item is None for item in resolved):
        return None
    return resolved


def write_repair_article(topic: dict, cfg: dict) -> tuple[dict, str]:
    """Use only Gemini for this repair, with one longer-content retry."""
    cfg.setdefault("ai", {})["primary"] = "gemini"
    cfg["ai"]["fallbacks"] = []
    content_cfg = cfg.setdefault("content", {})
    original_target = int(content_cfg.get("target_words") or 1100)
    # Keep the project's published quality floor: 600 words minimum.
    # Aim for the configured 1,100-word target without inventing a higher gate.
    content_cfg["min_words"] = 600
    # Be explicit about the owner's ban on all review/rating/testimonial content.
    for item in topic.get("items", []):
        strict_note = (
            "STRICT EDITORIAL RULE: Do not mention customer reviews, user opinions, "
            "buyer feedback, ratings, stars, scores, review counts, popularity, "
            "or what customers say in any form. Do not paraphrase or summarize them. "
            "Discuss only product listing facts supplied here and practical checks."
        )
        existing = str(item.get("notes") or "").strip()
        item["notes"] = (existing + " " + strict_note).strip()
    last_error = None

    for target in (max(original_target, 1100), max(original_target, 1250), max(original_target, 1400)):
        content_cfg["target_words"] = target
        try:
            return write_article(topic, cfg)
        except AIError as exc:
            last_error = exc
            log.warning(
                "Gemini repair attempt at %d words failed | %s | %s",
                target, topic.get("slug", ""), str(exc)[:180]
            )

    raise AIError(f"Gemini did not produce a valid repair after two attempts: {last_error}")


def topic_from_meta(meta: dict, resolved_items: list[dict]) -> dict:
    """Build a writer topic from queued metadata and verified local product data."""
    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "meta_description": meta.get("meta_description", ""),
        "template": meta.get("template", "review"),
        "niche": meta.get("niche", "general"),
        "keyword": meta.get("keyword") or meta["title"],
        "labels": list(meta.get("labels") or []),
        "items": resolved_items,
    }


def rebuild(limit: int = 8) -> dict:
    cfg = load_config()
    # For this queue-repair batch, lead with the Gemini Lite model that passed
    # the live smoke test; retain OpenRouter as the independent fallback.
    cfg.setdefault("ai", {})["primary"] = "gemini"
    cfg["ai"]["fallbacks"] = []
    aff = Affiliate(cfg)
    cache = _load_cache()
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
        # Keep any V4 post that already passes the project's 600-word floor.
        # Only repair legacy layouts or V4 drafts that are genuinely too thin.
        if (
            int(meta.get("layout_version") or 0) >= 4
            and int(meta.get("words") or 0) >= 600
        ):
            continue
        resolved_items = resolved_items_for_meta(meta, cache, aff)
        if resolved_items:
            candidates.append((meta, resolved_items))

    candidates = sorted(candidates, key=lambda pair: str(pair[0].get("slug")))[:max(1, min(int(limit), 16))]
    if not candidates:
        raise SystemExit("No legacy articles with fully verified existing product data were found.")

    results = []
    for meta, resolved_items in candidates:
        slug = meta["slug"]
        try:
            topic = topic_from_meta(meta, resolved_items)
            article, provider = write_repair_article(topic, cfg)
            article["_source"] = f"ai:{provider}"
            article["_provider"] = provider

            # The fixed renderer creates only green, tracked Amazon product buttons.
            # No images or product facts are fetched during this repair.
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
        except Exception as exc:  # one provider or article failure must not discard the other upgrades
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
    parser.add_argument("--limit", type=int, default=8, help="maximum legacy articles to rebuild (hard cap: 16)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)
    rebuild(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
