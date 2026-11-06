"""Resolve Amazon products to verified ASINs before a post can enter auto-publish.

Uses the Junglee Amazon Product Scraper Actor on Apify. Only product titles and
ASINs are retained for resolution; scraped prices, ratings, and review counts
are deliberately ignored.
"""
from __future__ import annotations

import html as html_lib
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
APIFY_ACTOR_ID = "junglee~amazon-crawler"
APIFY_ENDPOINT = (
    "https://api.apify.com/v2/actors/"
    f"{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
)
# The synchronous Apify endpoint waits for at most five minutes. Keeping each
# run to three Amazon search URLs avoids turning a normal article into a long run.
MAX_SEARCH_URLS_PER_RUN = 3
MAX_RESULTS_PER_SEARCH = 5
MAX_SEARCH_PAGES_PER_START_URL = 1


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
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _normal_search_term(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _amazon_search_url(term: str) -> str:
    return "https://www.amazon.com/s?" + parse.urlencode({"k": term})


def _search_key(value: str) -> str:
    """Normalize an Amazon search URL to its keyword so Apify rows can be grouped."""
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = parse.urlparse(value)
        query = parse.parse_qs(parsed.query)
        term = query.get("k") or query.get("keywords")
        if term:
            return "q:" + _normal_search_term(term[0])
    except ValueError:
        pass
    return value.rstrip("/").lower()


def _row_source_url(row: dict) -> str:
    """Read the start URL recorded by the Actor output, across its output shapes."""
    category_data = row.get("categoryPageData")
    if isinstance(category_data, dict) and category_data.get("categoryUrl"):
        return str(category_data["categoryUrl"])

    source = row.get("input") or row.get("inputUrl") or row.get("startUrl") or ""
    if isinstance(source, dict):
        source = source.get("url") or source.get("href") or ""
    return str(source)


def _extract_asin(row: dict) -> str:
    asin = str(row.get("asin") or row.get("originalAsin") or "").strip().upper()
    if ASIN_RE.fullmatch(asin):
        return asin
    product_url = str(row.get("url") or row.get("unNormalizedProductUrl") or "")
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", product_url, re.I)
    return match.group(1).upper() if match else ""


def _apify_search_batch(items: list[dict], api_token: str) -> dict[str, list[dict]]:
    """Run a small batch of Amazon search URLs and group rows by their source query."""
    unique_urls: dict[str, str] = {}
    for item in items:
        term = str(item.get("search_term") or item.get("display_name") or item.get("name") or "").strip()
        if not term:
            continue
        url = _amazon_search_url(term)
        unique_urls.setdefault(_search_key(url), url)

    if not unique_urls:
        return {}

    payload = {
        "categoryOrProductUrls": [{"url": url} for url in unique_urls.values()],
        "maxItemsPerStartUrl": MAX_RESULTS_PER_SEARCH,
        "maxSearchPagesPerStartUrl": MAX_SEARCH_PAGES_PER_START_URL,
        "language": "en",
        # The amazon.com URL determines the marketplace. Let Apify select a
        # suitable proxy automatically; do not send countryCode and zipCode.
        "proxyCountry": "AUTO_SELECT_PROXY_COUNTRY",
        # We need only title + ASIN. Full product pages, prices, ratings and
        # review counts are unnecessary for direct-link generation.
        "scrapeProductDetails": False,
        "maxProductVariantsAsSeparateResults": 0,
        "maxOffers": 0,
    }
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        APIFY_ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "EasyLifeHubProductResolver/2.0",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=310) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ProductResolutionError(f"Apify Actor HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProductResolutionError(f"Apify Actor request failed: {exc}") from exc

    if isinstance(data, dict):
        message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        message = message or data.get("message") or data.get("errorMessage")
        raise ProductResolutionError(
            f"Apify Actor returned an unexpected response: {str(message or data)[:400]}"
        )
    if not isinstance(data, list):
        raise ProductResolutionError("Apify Actor response was not a dataset item list")

    grouped: dict[str, list[dict]] = {key: [] for key in unique_urls}
    for row in data:
        if not isinstance(row, dict):
            continue
        key = _search_key(_row_source_url(row))
        if key in grouped:
            grouped[key].append(row)

    # A single-query run can still be safely handled if an Actor output revision
    # omits its source URL. Never mix rows across multiple product queries.
    if len(grouped) == 1 and data and not any(grouped.values()):
        only_key = next(iter(grouped))
        grouped[only_key] = [row for row in data if isinstance(row, dict)]

    return grouped


def _cache_key(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def _trusted_item(item: dict) -> bool:
    asin = str(item.get("asin") or "").strip().upper()
    verified_title = str(item.get("verified_product_title") or "")
    name = str(item.get("display_name") or item.get("name") or "").strip()
    return (
        item.get("asin_verified") is True
        and bool(ASIN_RE.fullmatch(asin))
        and title_matches(name, verified_title)
    )


def _apply_candidate(item: dict, rows: list[dict]) -> dict | None:
    name = str(item.get("display_name") or item.get("name") or "").strip()
    matches = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        asin = _extract_asin(row)
        if asin and title and title_matches(name, title):
            matches.append((asin, title))
    if not matches:
        return None

    # First qualifying result from this product's own Amazon search is used.
    asin, verified_title = matches[0]
    result = dict(item)
    result["asin"] = asin
    result["asin_verified"] = True
    result["verified_product_title"] = verified_title
    return result


def resolve_items(
    items: list[dict],
    api_token: str | None = None,
    cache: dict | None = None,
    allow_unresolved: bool = False,
) -> list[dict]:
    """Resolve products in batches, refusing to accept a weak or mismatched title."""
    api_token = (
        api_token if api_token is not None else os.environ.get("APIFY_API_TOKEN", "")
    ).strip()
    cache = cache if cache is not None else _load_cache()
    resolved = [dict(item) for item in items]
    pending_indexes: list[int] = []
    errors: list[str] = []

    for index, item in enumerate(resolved):
        name = str(item.get("display_name") or item.get("name") or "").strip()
        if not name:
            errors.append("product item has no name")
            continue
        if _trusted_item(item):
            continue
        cached = cache.get(_cache_key(name))
        if (
            isinstance(cached, dict)
            and ASIN_RE.fullmatch(str(cached.get("asin", "")).upper())
            and title_matches(name, str(cached.get("title") or ""))
        ):
            resolved[index]["asin"] = str(cached["asin"]).upper()
            resolved[index]["asin_verified"] = True
            resolved[index]["verified_product_title"] = str(cached["title"])
            continue
        pending_indexes.append(index)

    if pending_indexes and not api_token:
        raise ProductResolutionError(
            "APIFY_API_TOKEN is missing; refusing to publish an unverified product URL"
        )

    for start in range(0, len(pending_indexes), MAX_SEARCH_URLS_PER_RUN):
        batch_indexes = pending_indexes[start : start + MAX_SEARCH_URLS_PER_RUN]
        batch_items = [resolved[index] for index in batch_indexes]
        grouped = _apify_search_batch(batch_items, api_token) if batch_items else {}

        for index in batch_indexes:
            item = resolved[index]
            term = str(item.get("search_term") or item.get("display_name") or item.get("name") or "").strip()
            key = _search_key(_amazon_search_url(term))
            candidate = _apply_candidate(item, grouped.get(key, []))
            if candidate is None:
                message = f"no exact Amazon product-title match for: {item.get('display_name') or item.get('name')}"
                errors.append(message)
                log.warning("product resolution failed | %s", message)
                continue

            resolved[index] = candidate
            cache[_cache_key(str(item.get("display_name") or item.get("name") or ""))] = {
                "asin": candidate["asin"],
                "title": candidate["verified_product_title"],
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        _save_cache(cache)

    if errors and not allow_unresolved:
        raise ProductResolutionError("; ".join(errors[:4]))
    return resolved


def resolve_item(item: dict, api_token: str | None = None, cache: dict | None = None) -> dict:
    """Compatibility helper for callers that resolve one product at a time."""
    return resolve_items([item], api_token=api_token, cache=cache)[0]


def resolve_topic_items(topic: dict) -> dict:
    """Resolve every product in a topic or reject the entire article."""
    items = topic.get("items") or []
    if not items:
        raise ProductResolutionError(f"{topic.get('slug', 'topic')}: no products to resolve")
    topic["items"] = resolve_items(items)
    return topic


def product_cards_html(items: list[dict], affiliate) -> str:
    cards = []
    for item in items:
        if not _trusted_item(item):
            raise ProductResolutionError(f"unverified ASIN for {item.get('name', 'product')}")
        cards.append(affiliate.product_box(item, label="View Product on Amazon"))
    return (
        '<section class="product-links">'
        '<h2 style="background-color:#f1f3f4;padding:10px 15px;border-left:5px solid #2e7d32;'
        'font-size:20px;color:#202124;margin-top:30px;margin-bottom:15px;">Products in This Guide</h2>'
        + "".join(cards) + "</section>"
    )


def backfill_queue() -> dict:
    """Resolve queued product items and rewrite verified direct links in HTML/meta files."""
    from affiliate import Affiliate
    from util import QUEUE_DIR, load_config
    from render import rebuild_index

    cfg = load_config()
    aff = Affiliate(cfg)
    if not os.environ.get("APIFY_API_TOKEN", "").strip():
        raise ProductResolutionError(
            "APIFY_API_TOKEN is missing. Add it to GitHub Actions secrets before running the backfill."
        )

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
        new_items = resolve_items(items, cache=cache, allow_unresolved=True)
        ok = all(_trusted_item(item) for item in new_items)
        unresolved += sum(1 for item in new_items if not _trusted_item(item))

        html_path = QUEUE_DIR / str(meta.get("html_file") or f"{meta.get('slug')}.html")
        if ok and html_path.exists():
            body = html_path.read_text(encoding="utf-8")
            for item, old_url in zip(new_items, old_urls):
                new_url = aff.url_for(item)
                if old_url and new_url:
                    body = body.replace(html_lib.escape(old_url, quote=True), html_lib.escape(new_url, quote=True))
                    body = body.replace(old_url, html_lib.escape(new_url, quote=True))
            if 'class="product-links"' not in body:
                body += product_cards_html(new_items, aff)
            html_path.write_text(body, encoding="utf-8")
            meta["items"] = [{**item, "url": aff.url_for(item)} for item in new_items]
            meta["product_links_verified"] = True
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
