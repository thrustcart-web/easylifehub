#!/usr/bin/env python3
"""Daily, low-cost external product-availability evidence review for Honest Trove.

Searches third-party/manufacturer sources through Tavily. It does NOT crawl
Amazon product pages, infer Amazon stock from third-party inventory, or modify
Blogger links. Its report is research evidence for later authorized checking.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent.parent
QUEUE_INDEX = ROOT / "content" / "queue" / "index.json"
REPORT_DIR = ROOT / "reports" / "product-link-health"
STATE_PATH = REPORT_DIR / "state.json"
BLOG_URL = os.environ.get("BLOG_URL", "https://www.honesttrove.com").rstrip("/")
TAVILY_ENDPOINT = "https://api.tavily.com/search"
DEFAULT_DAILY_QUERIES = 15
HARD_DAILY_QUERY_CAP = 20
MAX_FEED_RESULTS = 150

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
AMAZON_PRODUCT_RE = re.compile(
    r"https?://(?:www\.)?amazon\.[a-z.]+/(?:dp|gp/product)/([A-Z0-9]{10})",
    re.IGNORECASE,
)
ANCHOR_RE = re.compile(r"<a\b[^>]*\bhref\s*=\s*(['\"])(.*?)\1[^>]*>(.*?)</a\s*>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
NEGATIVE_STOCK_RE = re.compile(
    r"\b(?:out\s+of\s+stock|currently\s+unavailable|sold\s+out|"
    r"temporarily\s+unavailable|discontinued|no\s+longer\s+available)\b",
    re.I,
)
POSITIVE_STOCK_RE = re.compile(
    r"\b(?:in\s+stock|available\s+now|available\s+for\s+purchase|"
    r"add\s+to\s+cart|buy\s+now|ready\s+to\s+ship)\b",
    re.I,
)
PRICE_RE = re.compile(r"(?<![\w.])\$\s*([\d,]+(?:\.\d{1,2})?)")
MODEL_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9-]{3,}\b)(?=[A-Z0-9-]*\d)[A-Z0-9-]+\b", re.I)


class TavilyAPIError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _json_load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _fetch_json(url: str, headers: dict[str, str] | None = None,
                payload: dict | None = None, timeout: int = 35) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        url, data=data, headers=headers or {},
        method="POST" if data is not None else "GET",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise TavilyAPIError(exc.code, detail) from exc
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Network request failed for {parse.urlparse(url).netloc}: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("A remote endpoint returned invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("A remote endpoint returned an unexpected JSON structure.")
    return value


def _field_text(value: Any) -> str:
    return str(value.get("$t") or "") if isinstance(value, dict) else str(value or "")


def _normal_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _anchor_asins(html: str) -> dict[str, str]:
    """Return direct Amazon product ASINs and their visible CTA label."""
    found: dict[str, str] = {}
    for match in ANCHOR_RE.finditer(str(html or "")):
        href = parse.unquote(match.group(2).replace("&amp;", "&")).strip()
        host = (parse.urlparse(href).hostname or "").lower()
        if not (host == "amazon.com" or host.endswith(".amazon.com") or
                re.fullmatch(r"amazon\.[a-z.]+", host)):
            continue
        asin_match = AMAZON_PRODUCT_RE.search(href)
        if not asin_match:
            continue
        asin = asin_match.group(1).upper()
        if ASIN_RE.fullmatch(asin):
            label = re.sub(r"\s+", " ", TAG_RE.sub(" ", match.group(3))).strip()
            found.setdefault(asin, label[:160])
    return found


def load_published_feed(blog_url: str = BLOG_URL) -> list[dict]:
    """Read the public Blogger JSON feed; only published posts appear here."""
    url = (
        f"{blog_url}/feeds/posts/default?"
        + parse.urlencode({"alt": "json", "max-results": str(MAX_FEED_RESULTS)})
    )
    data = _fetch_json(url, headers={"User-Agent": "HonestTroveProductReview/1.0"})
    entries = (data.get("feed") or {}).get("entry") or []
    if not isinstance(entries, list):
        raise RuntimeError("Blogger feed did not contain a valid entry list.")
    posts = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = _field_text(entry.get("title"))
        content = _field_text(entry.get("content"))
        if not title or not content:
            continue
        alternate = ""
        for link in entry.get("link") or []:
            if isinstance(link, dict) and link.get("rel") == "alternate":
                alternate = str(link.get("href") or "")
                break
        posts.append({
            "title": title, "content": content, "url": alternate,
            "published": _field_text(entry.get("published")),
        })
    if not posts:
        raise RuntimeError("Blogger feed returned no readable published articles; no report was written.")
    return posts


def build_product_inventory(posts: list[dict], index: dict) -> list[dict]:
    """Join published posts to queue metadata using title and direct-link ASIN."""
    metadata_by_title = {
        _normal_title(str(meta.get("title") or "")): meta
        for meta in (index.get("posts") or [])
        if isinstance(meta, dict) and meta.get("title")
    }
    products: dict[str, dict] = {}
    for post in posts:
        meta = metadata_by_title.get(_normal_title(post.get("title", ""))) or {}
        items_by_asin = {
            str(item.get("asin") or "").upper(): item
            for item in (meta.get("items") or [])
            if isinstance(item, dict) and ASIN_RE.fullmatch(str(item.get("asin") or "").upper())
        }
        for asin, anchor_label in _anchor_asins(post.get("content", "")).items():
            item = items_by_asin.get(asin, {})
            record = products.setdefault(asin, {
                "asin": asin,
                "product_name": str(item.get("name") or anchor_label or post.get("title") or ""),
                "verified_product_title": str(item.get("verified_product_title") or ""),
                "search_term": str(item.get("search_term") or ""),
                "posts": [],
            })
            record["posts"].append({
                "title": post.get("title", ""),
                "url": post.get("url", ""),
                "published": post.get("published", ""),
            })
            if not record["verified_product_title"] and item.get("verified_product_title"):
                record["verified_product_title"] = str(item["verified_product_title"])
            if not record["search_term"] and item.get("search_term"):
                record["search_term"] = str(item["search_term"])
    return sorted(products.values(), key=lambda item: item["asin"])


def _source_is_amazon(url: str) -> bool:
    host = (parse.urlparse(str(url or "")).hostname or "").lower()
    return (
        host == "amazon.com" or host.endswith(".amazon.com")
        or bool(re.fullmatch(r"(?:www\.)?amazon\.[a-z.]+", host))
        or host in {"amzn.to", "amzn.com"}
    )


def _identity_evidence(product: dict, result: dict) -> str:
    """Require exact ASIN or exact verified title/model evidence in the result."""
    text = " ".join([
        str(result.get("title") or ""),
        str(result.get("content") or ""),
        str(result.get("url") or ""),
    ]).casefold()
    asin = str(product.get("asin") or "").casefold()
    if asin and asin in text:
        return "ASIN_EXACT"
    verified_title = str(product.get("verified_product_title") or "").strip()
    if verified_title and verified_title.casefold() in text:
        return "VERIFIED_TITLE_EXACT"
    for token in MODEL_TOKEN_RE.findall(verified_title):
        if token.casefold() in text:
            return "MODEL_TOKEN_MATCH"
    name = str(product.get("product_name") or "").strip()
    if len(name) >= 8 and name.casefold() in text:
        return "PRODUCT_NAME_EXACT"
    return "NO_EXACT_IDENTITY_EVIDENCE"


def _stock_signal(text: str) -> str:
    # Normalize negated positive wording before testing the positive pattern.
    text = re.sub(r"\\bnot\\s+in\\s+stock\\b", "out of stock", str(text or ""), flags=re.I)
    negative = bool(NEGATIVE_STOCK_RE.search(text))
    positive = bool(POSITIVE_STOCK_RE.search(text))
    if negative and positive:
        return "CONFLICTING"
    if negative:
        return "EXTERNAL_OUT_OF_STOCK_SIGNAL"
    if positive:
        return "EXTERNAL_IN_STOCK_SIGNAL"
    return "NO_EXPLICIT_STOCK_SIGNAL"


def _price_over_50_usd(text: str) -> bool | None:
    values = []
    for raw in PRICE_RE.findall(text or ""):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return any(value > 50 for value in values) if values else None


def analyse_tavily_results(product: dict, payload: dict) -> dict:
    sources = []
    for result in payload.get("results") or []:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        if not url or _source_is_amazon(url):
            continue
        title = str(result.get("title") or "")[:240]
        snippet = re.sub(r"\s+", " ", str(result.get("content") or "")).strip()[:420]
        identity = _identity_evidence(product, {"title": title, "content": snippet, "url": url})
        sources.append({
            "domain": (parse.urlparse(url).hostname or "").lower(),
            "title": title,
            "url": url,
            "published_date": result.get("published_date"),
            "identity_evidence": identity,
            "identity_confirmed": identity != "NO_EXACT_IDENTITY_EVIDENCE",
            "external_stock_signal": _stock_signal(f"{title} {snippet}"),
            "price_over_50_usd_signal": _price_over_50_usd(f"{title} {snippet}"),
            "excerpt": snippet,
        })

    confirmed = [item for item in sources if item["identity_confirmed"]]
    positive = sorted({
        item["domain"] for item in confirmed
        if item["external_stock_signal"] == "EXTERNAL_IN_STOCK_SIGNAL"
    })
    negative = sorted({
        item["domain"] for item in confirmed
        if item["external_stock_signal"] == "EXTERNAL_OUT_OF_STOCK_SIGNAL"
    })
    if positive and negative:
        summary = "CONFLICTING_EXTERNAL_SIGNALS"
    elif len(positive) >= 2:
        summary = "MULTIPLE_EXTERNAL_IN_STOCK_SIGNALS"
    elif len(positive) == 1:
        summary = "SINGLE_EXTERNAL_IN_STOCK_SIGNAL"
    elif len(negative) >= 2:
        summary = "MULTIPLE_EXTERNAL_OUT_OF_STOCK_SIGNALS"
    elif len(negative) == 1:
        summary = "SINGLE_EXTERNAL_OUT_OF_STOCK_SIGNAL"
    elif confirmed:
        summary = "IDENTITY_FOUND_NO_STOCK_SIGNAL"
    else:
        summary = "NO_MATCHING_EXTERNAL_SOURCE"

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "asin": product["asin"],
        "product_name": product.get("product_name", ""),
        "verified_product_title": product.get("verified_product_title", ""),
        "posts": product.get("posts", []),
        "external_summary": summary,
        "positive_external_domains": positive,
        "negative_external_domains": negative,
        "amazon_inventory_verified": False,
        "automatic_replacement_allowed": False,
        "manual_amazon_confirmation_required": True,
        "tavily_credits_reported": usage.get("credits"),
        "sources": sources,
    }


def _search_tavily(product: dict, api_key: str) -> dict:
    identity = str(product.get("verified_product_title") or product.get("product_name") or "").strip()
    query = f'"{product["asin"]}" "{identity}" (in stock OR out of stock OR available OR sold out)'
    payload = {
        "query": query[:900],
        "search_depth": "basic",
        "topic": "general",
        "max_results": 8,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_usage": True,
        "language": "en",
        "safe_search": False,
    }
    return _fetch_json(
        TAVILY_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "HonestTroveProductReview/1.0",
        },
        payload=payload,
    )


def _load_state() -> dict:
    value = _json_load(STATE_PATH, {})
    return value if isinstance(value, dict) else {}


def _next_batch(products: list[dict], cursor: int, limit: int) -> tuple[list[dict], int]:
    if not products:
        return [], 0
    cursor %= len(products)
    count = min(max(0, limit), len(products))
    selected = [products[(cursor + offset) % len(products)] for offset in range(count)]
    return selected, (cursor + count) % len(products)


def _write_summary(report: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## Honest Trove — Daily Product Link Review",
        "",
        f"- Published posts scanned: **{report['published_posts_scanned']}**",
        f"- Unique Amazon products found: **{report['unique_products_found']}**",
        f"- Tavily searches completed: **{report['queries_completed']}**",
        f"- External in-stock signals: **{report['external_in_stock_signals']}**",
        f"- External out-of-stock signals: **{report['external_out_of_stock_signals']}**",
        "- Amazon stock confirmations: **0** (third-party search is not an Amazon inventory source)",
        "- Blogger links changed: **0** (automatic replacement is intentionally disabled)",
        "",
        "This report contains third-party evidence only. Verify an alternative through an authorized Amazon availability source before changing a Special Link.",
    ]
    with open(path, "a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def run_review(max_queries: int = DEFAULT_DAILY_QUERIES) -> dict:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY GitHub Actions secret is missing.")
    index = _json_load(QUEUE_INDEX, {})
    if not isinstance(index, dict):
        raise RuntimeError("Queue index is unavailable or invalid.")
    posts = load_published_feed()
    products = build_product_inventory(posts, index)
    if not products:
        raise RuntimeError("No direct Amazon product links were found in the public Blogger feed.")

    state = _load_state()
    cursor = int(state.get("cursor") or 0)
    limit = min(HARD_DAILY_QUERY_CAP, max(1, int(max_queries)))
    selected, _ = _next_batch(products, cursor, limit)
    results, errors = [], []
    queries_completed = 0
    next_cursor = cursor
    quota_exhausted = False

    for product in selected:
        try:
            payload = _search_tavily(product, api_key)
            results.append(analyse_tavily_results(product, payload))
            queries_completed += 1
            next_cursor = (next_cursor + 1) % len(products)
        except TavilyAPIError as exc:
            errors.append({
                "asin": product["asin"], "http_status": exc.status,
                "message": str(exc)[:500],
            })
            quota_exhausted = exc.status in {402, 432, 433}
            break
        except Exception as exc:  # noqa: BLE001 - preserve successful checks in this run
            errors.append({
                "asin": product["asin"],
                "message": f"{type(exc).__name__}: {str(exc)[:300]}",
            })
            break

    now = datetime.now(timezone.utc)
    report = {
        "schema_version": 1,
        "report_date_utc": now.strftime("%Y-%m-%d"),
        "created_at": now.isoformat(),
        "blog_url": BLOG_URL,
        "published_posts_scanned": len(posts),
        "unique_products_found": len(products),
        "queries_requested": limit,
        "queries_completed": queries_completed,
        "external_in_stock_signals": sum(
            item["external_summary"] in {
                "SINGLE_EXTERNAL_IN_STOCK_SIGNAL",
                "MULTIPLE_EXTERNAL_IN_STOCK_SIGNALS",
            } for item in results
        ),
        "external_out_of_stock_signals": sum(
            item["external_summary"] in {
                "SINGLE_EXTERNAL_OUT_OF_STOCK_SIGNAL",
                "MULTIPLE_EXTERNAL_OUT_OF_STOCK_SIGNALS",
            } for item in results
        ),
        "amazon_inventory_confirmations": 0,
        "automatic_blogger_changes": 0,
        "quota_or_api_errors": errors,
        "results": results,
        "policy": {
            "third_party_stock_is_not_amazon_stock": True,
            "amazon_pages_crawled_directly": False,
            "automatic_special_link_replacement": False,
        },
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"{now.strftime('%Y-%m-%d')}.json"
    _write_json(report_path, report)
    _write_json(STATE_PATH, {
        "cursor": next_cursor,
        "last_run_utc": now.isoformat(),
        "last_report": report_path.name,
        "total_products_seen": len(products),
        "last_successful_queries": queries_completed,
        "quota_exhausted": quota_exhausted,
    })
    _write_summary(report)
    print(json.dumps({
        "report": str(report_path.relative_to(ROOT)),
        "published_posts_scanned": report["published_posts_scanned"],
        "unique_products_found": report["unique_products_found"],
        "queries_completed": queries_completed,
        "external_in_stock_signals": report["external_in_stock_signals"],
        "external_out_of_stock_signals": report["external_out_of_stock_signals"],
        "amazon_inventory_confirmations": 0,
        "automatic_blogger_changes": 0,
        "errors": errors,
    }, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    run_review(int(os.environ.get("TAVILY_DAILY_MAX_QUERIES", DEFAULT_DAILY_QUERIES)))
