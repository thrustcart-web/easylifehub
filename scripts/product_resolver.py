"""Resolve verified Amazon ASINs before an article can enter auto-publish.

Amazon Creators API is the primary source. SerpApi is a guarded fallback only;
Apify remains available solely when explicitly selected by an operator.
Only product titles and ASINs are retained.
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
CREATORS_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
CREATORS_SEARCH_URL = "https://creatorsapi.amazon/catalog/v1/searchItems"
_CREATORS_ACCESS_TOKEN = ""
_CREATORS_TOKEN_EXPIRES_AT = 0.0
MAX_RESULTS_PER_SEARCH = 3
# API-level billing safeguards for every individual product search.
# The user's observed Result rate was $0.005/event. This caps a run at three
# billable results ($0.015) and $0.015 total across every Apify pricing model.
APIFY_MAX_TOTAL_CHARGE_USD = "0.015"
APIFY_ENDPOINT = (
    "https://api.apify.com/v2/acts/"
    f"{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
    + "?"
    + parse.urlencode({
        "maxItems": str(MAX_RESULTS_PER_SEARCH),
        "maxTotalChargeUsd": APIFY_MAX_TOTAL_CHARGE_USD,
        "timeout": "300",
    })
)


class ProductResolutionError(RuntimeError):
    pass


def _normal_tokens(value: str) -> set[str]:
    stop = {"a", "an", "and", "the", "for", "with", "of", "to", "from", "best", "buying", "guide"}
    out = set()
    # Apify sometimes concatenates a brand and the next word (e.g. "MarbrassePen").
    normalized = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value or ""))
    for token in TOKEN_RE.findall(normalized.lower()):
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


def _normal_name(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _amazon_search_url(term: str) -> str:
    return "https://www.amazon.com/s?" + parse.urlencode({"k": term})


def _extract_asin(row: dict) -> str:
    asin = str(row.get("asin") or row.get("originalAsin") or "").strip().upper()
    if ASIN_RE.fullmatch(asin):
        return asin
    product_url = str(row.get("url") or row.get("unNormalizedProductUrl") or "")
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", product_url, re.I)
    return match.group(1).upper() if match else ""


def _selected_provider() -> str:
    """Select the explicit workflow override, otherwise use config.json."""
    provider = os.environ.get("PRODUCT_DISCOVERY_PROVIDER", "").strip().lower()
    if not provider:
        try:
            cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
            provider = str((cfg.get("product_discovery") or {}).get("provider") or "creators_api").strip().lower()
        except (OSError, json.JSONDecodeError):
            provider = "creators_api"
    if provider not in {"apify", "serpapi", "creators_api"}:
        raise ProductResolutionError(
            "PRODUCT_DISCOVERY_PROVIDER must be 'creators_api', 'serpapi', or 'apify'"
        )
    return provider


def _fallback_provider_names(primary: str) -> list[str]:
    """Return configured, safe fallback providers; Apify is never automatic."""
    if primary != "creators_api":
        return []
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        configured = (cfg.get("product_discovery") or {}).get("fallback_providers") or []
    except (OSError, json.JSONDecodeError):
        configured = []
    return [name for name in configured if name == "serpapi"]


def _is_global_provider_error(exc: Exception) -> bool:
    """Detect account/quota/auth errors where repeating the same provider is wasteful."""
    message = str(exc)
    return bool(re.search(
        r"\\bHTTP\\s+(?:400|401|402|403|429)\\b|"
        r"(?:Apify Actor|SerpApi|Creators API) error:|"
        r"\\b(?:quota exhausted|rate.?limit|monthly usage limit|unauthorized|forbidden)\\b",
        message,
        re.I,
    ))


def _provider_api_key(provider: str) -> str:
    if provider == "serpapi":
        return os.environ.get("SERPAPI_API_KEY", "").strip()
    if provider == "creators_api":
        client_id = os.environ.get("AMAZON_CREATORS_CLIENT_ID", "").strip()
        client_secret = os.environ.get("AMAZON_CREATORS_CLIENT_SECRET", "").strip()
        return "configured" if client_id and client_secret else ""
    return (
        os.environ.get("TEST_API")
        or os.environ.get("APIFY_API_TOKEN", "")
    ).strip()


def _provider_secret_name(provider: str) -> str:
    if provider == "serpapi":
        return "SERPAPI_API_KEY"
    if provider == "creators_api":
        return "AMAZON_CREATORS_CLIENT_ID and AMAZON_CREATORS_CLIENT_SECRET"
    return "TEST_API"


def _provider_spending_enabled(provider: str) -> bool:
    # Creators API is quota-limited, not billed per search like SerpApi/Apify.
    if provider == "creators_api":
        return True
    variable = "SERPAPI_SPEND_ENABLED" if provider == "serpapi" else "APIFY_SPEND_ENABLED"
    return os.environ.get(variable, "false").strip().lower() in {"1", "true", "yes"}


def _search_amazon_apify(product_name: str, api_token: str) -> list[dict]:
    """Run one isolated Amazon US search through the Apify Actor."""
    payload = {
        "categoryOrProductUrls": [{"url": _amazon_search_url(product_name)}],
        "maxItemsPerStartUrl": MAX_RESULTS_PER_SEARCH,
        "maxSearchPagesPerStartUrl": 1,
        "language": "en",
        # Do NOT set countryCode, zipCode, proxyCountry, or delivery-location
        # fields: they created a separate, expensive event in the user's account.
        "scrapeProductDetails": False,
        "maxProductVariantsAsSeparateResults": 0,
        "maxOffers": 0,
    }
    req = request.Request(
        APIFY_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "EasyLifeHubProductResolver/3.0",
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
        error_data = data.get("error")
        message = error_data.get("message") if isinstance(error_data, dict) else error_data
        message = message or data.get("message") or data.get("errorMessage")
        raise ProductResolutionError(
            f"Apify Actor returned an unexpected response: {str(message or data)[:400]}"
        )
    if not isinstance(data, list):
        raise ProductResolutionError("Apify Actor response was not a dataset item list")
    return [row for row in data if isinstance(row, dict)]


def _search_amazon_serpapi(product_name: str, api_key: str) -> list[dict]:
    """Search Amazon through SerpApi and retain only the title and ASIN."""
    params = {
        "engine": "amazon",
        "k": product_name,
        "amazon_domain": "amazon.com",
        "language": "en_US",
        "api_key": api_key,
        "no_cache": "false",
    }
    url = "https://serpapi.com/search.json?" + parse.urlencode(params)
    req = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "EasyLifeHubProductResolver/3.0",
        },
        method="GET",
    )

    try:
        with request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ProductResolutionError(f"SerpApi HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProductResolutionError(f"SerpApi request failed: {exc}") from exc

    if not isinstance(data, dict):
        raise ProductResolutionError("SerpApi response was not a JSON object")
    if data.get("error"):
        raise ProductResolutionError(f"SerpApi error: {str(data['error'])[:400]}")
    metadata = data.get("search_metadata") or {}
    if isinstance(metadata, dict) and metadata.get("status") == "Error":
        raise ProductResolutionError("SerpApi reported an unsuccessful search")
    results = data.get("organic_results") or []
    if not isinstance(results, list):
        raise ProductResolutionError("SerpApi organic_results was not a list")

    # Deliberately discard all extra source fields, including price, rating,
    # reviews, shipping, and images. Only product identity is used downstream.
    rows = []
    for item in results:
        if not isinstance(item, dict) or item.get("sponsored") is True:
            continue
        title = str(item.get("title") or "").strip()
        asin = str(item.get("asin") or "").strip().upper()
        if title and ASIN_RE.fullmatch(asin):
            rows.append({"title": title, "asin": asin})
    return rows



def _creators_partner_settings() -> tuple[str, str]:
    """Read the marketplace and Associate tag without exposing either in logs."""
    # config.json is authoritative because Affiliate builds published URLs from
    # that same value. Environment variables are only fallbacks for local tests.
    env_tag = os.environ.get("AFFILIATE_TAG", "").strip() or os.environ.get("AMAZON_PARTNER_TAG", "").strip()
    tag = ""
    marketplace = os.environ.get("AMAZON_MARKETPLACE", "").strip()
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        affiliate = cfg.get("affiliate") or {}
        tag = str(affiliate.get("tag") or "").strip() or env_tag
        market_code = str(affiliate.get("marketplace") or "com").strip().lower()
        market_map = {
            "com": "www.amazon.com", "co.uk": "www.amazon.co.uk", "de": "www.amazon.de",
            "fr": "www.amazon.fr", "ca": "www.amazon.ca", "eg": "www.amazon.eg",
            "sa": "www.amazon.sa", "ae": "www.amazon.ae", "in": "www.amazon.in",
        }
        marketplace = marketplace or market_map.get(market_code, "www.amazon.com")
    except (OSError, json.JSONDecodeError):
        tag = env_tag
        marketplace = marketplace or "www.amazon.com"
    if not tag or tag.startswith("YOURTAG"):
        raise ProductResolutionError("Creators API needs a valid Associate tag in config.json or AFFILIATE_TAG")
    return tag, marketplace


def _creators_access_token() -> str:
    """Obtain and cache the Creators API OAuth token for this workflow process."""
    global _CREATORS_ACCESS_TOKEN, _CREATORS_TOKEN_EXPIRES_AT
    if _CREATORS_ACCESS_TOKEN and time.time() < (_CREATORS_TOKEN_EXPIRES_AT - 60):
        return _CREATORS_ACCESS_TOKEN
    client_id = os.environ.get("AMAZON_CREATORS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AMAZON_CREATORS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ProductResolutionError("Set AMAZON_CREATORS_CLIENT_ID and AMAZON_CREATORS_CLIENT_SECRET")
    token_url = os.environ.get("AMAZON_CREATORS_TOKEN_URL", "").strip() or CREATORS_TOKEN_URL
    payload = {
        "grant_type": "client_credentials", "client_id": client_id,
        "client_secret": client_secret, "scope": "creatorsapi::default",
    }
    req = request.Request(
        token_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST",
    )
    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ProductResolutionError(f"Creators API OAuth HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProductResolutionError(f"Creators API OAuth request failed: {exc}") from exc
    token = str(data.get("access_token") or "").strip() if isinstance(data, dict) else ""
    if not token:
        message = (data.get("error_description") or data.get("error") or data) if isinstance(data, dict) else data
        raise ProductResolutionError(f"Creators API OAuth returned no access token: {str(message)[:400]}")
    try:
        expires_in = max(120, int(data.get("expires_in", 3600)))
    except (TypeError, ValueError):
        expires_in = 3600
    _CREATORS_ACCESS_TOKEN = token
    _CREATORS_TOKEN_EXPIRES_AT = time.time() + expires_in
    return token


def _creators_rows_from_response(data: dict) -> list[dict]:
    """Keep only product identity fields from the official API response."""
    items = (data.get("searchResult") or {}).get("items") or []
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        asin = str(item.get("asin") or "").strip().upper()
        title_node = ((item.get("itemInfo") or {}).get("title") or {})
        title = str(title_node.get("displayValue") or "").strip()
        if title and ASIN_RE.fullmatch(asin):
            rows.append({"title": title, "asin": asin, "url": str(item.get("detailPageURL") or "")})
    return rows


def _search_amazon_creators_api(product_name: str) -> list[dict]:
    """Search Amazon's official Creators API (one request per unique query)."""
    token = _creators_access_token()
    partner_tag, marketplace = _creators_partner_settings()
    payload = {
        "keywords": product_name, "partnerTag": partner_tag, "marketplace": marketplace,
        "itemCount": 10, "resources": ["itemInfo.title"],
    }
    req = request.Request(
        CREATORS_SEARCH_URL, data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json", "x-marketplace": marketplace,
            "User-Agent": "EasyLifeHubProductResolver/4.0",
        }, method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise ProductResolutionError(f"Creators API HTTP {exc.code}: {detail}") from exc
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProductResolutionError(f"Creators API request failed: {exc}") from exc
    if not isinstance(data, dict):
        raise ProductResolutionError("Creators API response was not a JSON object")
    rows = _creators_rows_from_response(data)
    if not rows and data.get("errors"):
        raise ProductResolutionError(f"Creators API returned no valid items: {str(data.get('errors'))[:400]}")
    return rows

def _search_amazon_with_provider(product_name: str, provider: str, api_token: str = "") -> list[dict]:
    """Dispatch one query to a named provider without changing global selection."""
    if provider == "serpapi":
        if not api_token:
            raise ProductResolutionError("SERPAPI_API_KEY is missing")
        return _search_amazon_serpapi(product_name, api_token)
    if provider == "creators_api":
        return _search_amazon_creators_api(product_name)
    if provider == "apify":
        if not api_token:
            raise ProductResolutionError("TEST_API is missing")
        return _search_amazon_apify(product_name, api_token)
    raise ProductResolutionError(f"Unsupported Amazon product provider: {provider}")


def _search_amazon(product_name: str, api_token: str) -> list[dict]:
    """Dispatch to the configured primary discovery provider."""
    return _search_amazon_with_provider(product_name, _selected_provider(), api_token)

def _trusted_item(item: dict) -> bool:
    asin = str(item.get("asin") or "").strip().upper()
    verified_title = str(item.get("verified_product_title") or "")
    name = str(item.get("display_name") or item.get("name") or "").strip()
    return (
        item.get("asin_verified") is True
        and bool(ASIN_RE.fullmatch(asin))
        and title_matches(name, verified_title)
    )


def _apply_candidate(
    item: dict,
    rows: list[dict],
    allow_missing_leading_brand: bool = False,
) -> dict | None:
    """Accept an exact title, or optionally one omitted leading brand token.

    Some Amazon SERP listings omit the brand at the start of the title. In
    that case we still require every remaining meaningful name token to match,
    with at least four tokens remaining so a generic two-word category cannot
    accidentally validate an unrelated result.
    """
    name = str(item.get("display_name") or item.get("name") or "").strip()
    wanted = _normal_tokens(name)
    raw_name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    raw_tokens = [
        token.lower()
        for token in TOKEN_RE.findall(raw_name.lower())
        if token.lower() not in {"a", "an", "and", "the", "for", "with", "of", "to", "from", "best", "buying", "guide"}
    ]
    leading_token = raw_tokens[0] if raw_tokens else ""

    for row in rows:
        title = str(row.get("title") or "").strip()
        asin = _extract_asin(row)
        if not (asin and title):
            continue

        matches = title_matches(name, title)
        if not matches and allow_missing_leading_brand and leading_token:
            found = _normal_tokens(title)
            missing = wanted - found
            remaining = wanted - {leading_token}
            matches = (
                leading_token in wanted
                and missing == {leading_token}
                and len(remaining) >= 4
                and remaining.issubset(found)
            )

        if matches:
            result = dict(item)
            result["asin"] = asin
            result["asin_verified"] = True
            result["verified_product_title"] = title
            return result
    return None


def resolve_items(
    items: list[dict],
    api_token: str | None = None,
    cache: dict | None = None,
    allow_unresolved: bool = False,
) -> list[dict]:
    """Resolve products via Creators API first, using guarded SerpApi fallback."""
    provider = _selected_provider()
    api_token = (api_token if api_token is not None else _provider_api_key(provider)).strip()
    cache = cache if cache is not None else _load_cache()
    resolved = [dict(item) for item in items]
    errors = []
    pending: dict[str, list[int]] = {}

    for index, item in enumerate(resolved):
        name = str(item.get("display_name") or item.get("name") or "").strip()
        if not name:
            errors.append("product item has no name")
            continue
        if _trusted_item(item):
            continue

        cache_key = _normal_name(name)
        cached = cache.get(cache_key)
        if (
            isinstance(cached, dict)
            and ASIN_RE.fullmatch(str(cached.get("asin", "")).upper())
            and title_matches(name, str(cached.get("title") or ""))
        ):
            resolved[index]["asin"] = str(cached["asin"]).upper()
            resolved[index]["asin_verified"] = True
            resolved[index]["verified_product_title"] = str(cached["title"])
            continue
        pending.setdefault(_normal_name(str(item.get("search_term") or name)), []).append(index)

    fallback_providers = [
        name for name in _fallback_provider_names(provider)
        if _provider_spending_enabled(name) and bool(_provider_api_key(name))
    ]

    if pending and not api_token and not fallback_providers:
        raise ProductResolutionError(
            f"{_provider_secret_name(provider)} is missing; refusing to publish an unverified product URL"
        )
    if pending and provider in {"serpapi", "apify"}:
        if not api_token:
            raise ProductResolutionError(
                f"{_provider_secret_name(provider)} is missing; refusing to publish an unverified product URL"
            )
        if not _provider_spending_enabled(provider):
            spend_variable = "SERPAPI_SPEND_ENABLED" if provider == "serpapi" else "APIFY_SPEND_ENABLED"
            raise ProductResolutionError(
                f"{provider} spending is disabled. Set the GitHub Actions variable "
                f"{spend_variable}=true only after reviewing the account's search allowance."
            )

    def apply_rows(rows: list[dict], source: str, indexes: list[int]) -> list[int]:
        remaining = []
        for index in indexes:
            item = resolved[index]
            candidate = _apply_candidate(
                item,
                rows,
                allow_missing_leading_brand=(source == "serpapi"),
            )
            if candidate is None:
                remaining.append(index)
                continue
            resolved[index] = candidate
            cache[_normal_name(str(item.get("display_name") or item.get("name") or ""))] = {
                "asin": candidate["asin"],
                "title": candidate["verified_product_title"],
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        return remaining

    for search_term, indexes in pending.items():
        primary_error = None
        unresolved = list(indexes)

        try:
            if not api_token:
                raise ProductResolutionError(
                    f"{_provider_secret_name(provider)} is missing"
                )
            primary_rows = _search_amazon(search_term, api_token)
            unresolved = apply_rows(primary_rows, provider, unresolved)
        except ProductResolutionError as exc:
            primary_error = exc
            log.warning("Amazon primary search failed | %s | %s", search_term, exc)

        # A Creators API account-level error should stop the batch when there is
        # no explicitly enabled SerpApi fallback; never silently retry paid APIs.
        if unresolved and primary_error and _is_global_provider_error(primary_error) and not fallback_providers:
            raise primary_error

        fallback_errors = []
        for fallback_provider in fallback_providers:
            if not unresolved:
                break
            fallback_key = _provider_api_key(fallback_provider)
            try:
                fallback_rows = _search_amazon_with_provider(
                    search_term, fallback_provider, fallback_key
                )
            except ProductResolutionError as exc:
                fallback_errors.append(str(exc))
                log.warning(
                    "Amazon fallback search failed | provider=%s | %s",
                    fallback_provider, exc
                )
                if _is_global_provider_error(exc):
                    raise
                continue
            unresolved = apply_rows(fallback_rows, fallback_provider, unresolved)

        if unresolved:
            if primary_error and _is_global_provider_error(primary_error):
                raise primary_error
            for index in unresolved:
                name = resolved[index].get("display_name") or resolved[index].get("name")
                if primary_error:
                    message = f"{primary_error}; no verified fallback match for: {name}"
                elif fallback_errors:
                    message = f"{'; '.join(fallback_errors[:2])}; no exact Amazon product-title match for: {name}"
                else:
                    message = f"no exact Amazon product-title match for: {name}"
                errors.append(message)
                log.warning("product resolution failed | %s", message)
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
    """Resolve queued products and rewrite verified direct links in HTML/meta files."""
    from affiliate import Affiliate
    from util import QUEUE_DIR, load_config
    from render import rebuild_index

    cfg = load_config()
    aff = Affiliate(cfg)
    provider = _selected_provider()
    if not _provider_api_key(provider):
        raise ProductResolutionError(
            f"{_provider_secret_name(provider)} is missing. Add it to GitHub Actions secrets before running the backfill."
        )
    if not _provider_spending_enabled(provider):
        spend_variable = "SERPAPI_SPEND_ENABLED" if provider == "serpapi" else "APIFY_SPEND_ENABLED"
        raise ProductResolutionError(
            f"{provider} spending is disabled. Set GitHub Actions variable {spend_variable}=true before running the backfill."
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
