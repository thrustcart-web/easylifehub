"""Resolve Amazon products to verified ASINs before a post can enter auto-publish.

Uses SerpApi's Amazon Search endpoint. Only ASIN + product title are retained;
price, rating, and review data are deliberately ignored.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib import error, parse, request

from util import ROOT, log

CACHE_PATH = ROOT / ".cache" / "amazon_asins.json"
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
TOKEN_RE = re.compile(r"[a-z0-9]+")


class ProductResolutionError(RuntimeError):
    pass


def _normal_tokens(value: str) -> set[str]:
    stop = {"a", "an", "and", "the", "for", "with", "of", "to", "from", "best", "buying", "guide"}
    out = set()
    for token in TOKEN_RE.findall(str(value or "").lower()):
        if token in stop:
            continue
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        out.add(token)
    return out


def title_matches(product_name: str, result_title: str) -> bool:
    """Require every meaningful product-name token in the returned Amazon title."""
    wanted = _normal_tokens(product_name)
    found = _normal_tokens(result_title)
    return bool(wanted) and wanted.issubset(found)


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _search_amazon(product_name: str, api_key: str) -> dict:
    params = {
        "engine": "amazon",
        "amazon_domain": "amazon.com",
        "k": product_name,
        "api_key": api_key,
        "output": "json",
    }
    url = "https://serpapi.com/search.json?" + parse.urlencode(params)
    req = request.Request(url, headers={"User-Agent": "EasyLifeHubProductResolver/1.0"})
    try:
        with request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise ProductResolutionError(f"SerpApi HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProductResolutionError(f"SerpApi request failed: {exc}") from exc
    if data.get("error"):
        raise ProductResolutionError(f"SerpApi error: {str(data['error'])[:300]}")
    return data


def _candidate_rows(data: dict) -> list[dict]:
    rows = list(data.get("organic_results") or [])
    rows.extend(data.get("featured_products") or [])
    for group in data.get("featured_products") or []:
        if isinstance(group, dict):
            rows.extend(group.get("products") or [])
    return [row for row in rows if isinstance(row, dict)]


def resolve_item(item: dict, api_key: str | None = None, cache: dict | None = None) -> dict:
    """Return a copy of item with an ASIN verified against an Amazon result title."""
    api_key = (api_key if api_key is not None else os.environ.get("SERPAPI_API_KEY", "")).strip()
    name = str(item.get("display_name") or item.get("name") or "").strip()
    if not name:
        raise ProductResolutionError("product item has no name")

    cache = cache if cache is not None else _load_cache()
    key = " ".join(name.lower().split())
    supplied_asin = str(item.get("asin") or "").strip().upper()
    supplied_title = str(item.get("verified_product_title") or "")
    if item.get("asin_verified") is True and ASIN_RE.fullmatch(supplied_asin) and title_matches(name, supplied_title):
        return dict(item)
    cached = cache.get(key)
    if isinstance(cached, dict) and ASIN_RE.fullmatch(str(cached.get("asin", ""))) and title_matches(name, cached.get("title", "")):
        result = dict(item)
        result["asin"] = cached["asin"]
        result["asin_verified"] = True
        result["verified_product_title"] = cached["title"]
        return result

    if not api_key:
        # A prefilled identifier without a verification record is not trusted.
        raise ProductResolutionError("SERPAPI_API_KEY is missing; refusing to publish an unverified product URL")

    data = _search_amazon(str(item.get("search_term") or name), api_key)
    matches = []
    for row in _candidate_rows(data):
        title = str(row.get("title") or "").strip()
        asin = str(row.get("asin") or "").strip().upper()
        link = str(row.get("link_clean") or row.get("link") or "")
        if not ASIN_RE.fullmatch(asin):
            m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", link, re.I)
            asin = m.group(1).upper() if m else ""
        if ASIN_RE.fullmatch(asin) and title and title_matches(name, title):
            matches.append((asin, title))
    if not matches:
        raise ProductResolutionError(f"no exact Amazon product-title match for: {name}")

    # First qualifying organic result is the closest Amazon search result.
    asin, verified_title = matches[0]
    cache[key] = {"asin": asin, "title": verified_title, "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _save_cache(cache)

    result = dict(item)
    result["asin"] = asin
    result["asin_verified"] = True
    result["verified_product_title"] = verified_title
    return result


def resolve_topic_items(topic: dict) -> dict:
    """Resolve every product in a topic or reject the entire article."""
    items = topic.get("items") or []
    if not items:
        raise ProductResolutionError(f"{topic.get('slug', 'topic')}: no products to resolve")
    cache = _load_cache()
    resolved = []
    errors = []
    for item in items:
        try:
            resolved.append(resolve_item(item, cache=cache))
        except ProductResolutionError as exc:
            errors.append(str(exc))
    if errors:
        raise ProductResolutionError("; ".join(errors[:4]))
    topic["items"] = resolved
    return topic


def product_cards_html(items: list[dict], affiliate) -> str:
    cards = []
    for item in items:
        if not item.get("asin_verified"):
            raise ProductResolutionError(f"unverified ASIN for {item.get('name', 'product')}")
        cards.append(affiliate.product_box(item, label="View Product on Amazon"))
    return (
        '<section class="product-links">'
        '<h2 style="background-color:#f1f3f4;padding:10px 15px;border-left:5px solid #2e7d32;'
        'font-size:20px;color:#202124;margin-top:30px;margin-bottom:15px;">Products in This Guide</h2>'
        + "".join(cards) + "</section>"
    )


def backfill_queue() -> dict:
    """Resolve all queued product items and rewrite links in existing HTML/meta files."""
    import html as html_lib
    from affiliate import Affiliate
    from util import QUEUE_DIR, load_config
    from render import rebuild_index

    cfg = load_config()
    aff = Affiliate(cfg)
    cache = _load_cache()
    updated = 0
    unresolved = 0
    files = sorted(p for p in QUEUE_DIR.glob("*.json") if p.name != "index.json")

    for meta_path in files:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = meta.get("items") or []
        if not items:
            continue
        old_urls = [str(item.get("url") or "") for item in items]
        new_items = []
        ok = True
        for item in items:
            try:
                new_items.append(resolve_item(item, cache=cache))
            except ProductResolutionError as exc:
                log.warning("product link unresolved | %s | %s", meta.get("slug"), exc)
                ok = False
                unresolved += 1
                new_items.append(dict(item))

        html_path = QUEUE_DIR / str(meta.get("html_file") or f"{meta.get('slug')}.html")
        if ok and html_path.exists():
            body = html_path.read_text(encoding="utf-8")
            for item, old_url in zip(new_items, old_urls):
                new_url = aff.url_for(item)
                if old_url and new_url:
                    body = body.replace(html_lib.escape(old_url, quote=True), html_lib.escape(new_url, quote=True))
                    body = body.replace(old_url, html_lib.escape(new_url, quote=True))
            if "class=\"product-links\"" not in body:
                body += product_cards_html(new_items, aff)
            html_path.write_text(body, encoding="utf-8")
            meta["items"] = [
                {**item, "url": aff.url_for(item)}
                for item in new_items
            ]
            meta["product_links_verified"] = True
            # Preserve an existing manual review flag; only keep automated items eligible.
            if str(meta.get("source") or "").startswith("ai:") or meta.get("source") == "seed":
                meta["review_required"] = bool(meta.get("review_required", False))
            updated += 1
        else:
            meta["items"] = new_items
            meta["product_links_verified"] = False
            meta["review_required"] = True
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rebuild_index(cfg)
    _save_cache(cache)
    log.info("Product link backfill complete: updated=%d unresolved_items=%d", updated, unresolved)
    return {"updated_articles": updated, "unresolved_items": unresolved}
