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
CREATORS_GET_ITEMS_URL = "https://creatorsapi.amazon/catalog/v1/getItems"
_CREATORS_ACCESS_TOKEN = ""
_CREATORS_TOKEN_EXPIRES_AT = 0.0
STOCK_ALLOWED = {"IN_STOCK", "INSTOCK", "INSTOCKSCARCE"}
STOCK_MAX_AGE_SECONDS = 24 * 60 * 60
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
        r"\bHTTP\s+(?:400|401|402|403|429)\b|"
        r"(?:Apify Actor|SerpApi|Creators API) error:|"
        r"\b(?:quota exhausted|rate.?limit|monthly usage limit|unauthorized|forbidden)\b",
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


def _availability_from_listings(listings: list[dict]) -> str:
    """Return an explicit Amazon availability value; unknown is never treated as stock."""
    statuses = []
    for listing in listings or []:
        if not isinstance(listing, dict):
            continue
        condition = listing.get("condition") or {}
        condition_value = str(condition.get("value") or "").upper()
        if condition_value and condition_value != "NEW":
            continue
        availability = listing.get("availability") or {}
        status = str(availability.get("type") or "").strip().upper()
        if status:
            statuses.append(status)
    for preferred in ("IN_STOCK", "INSTOCK", "INSTOCKSCARCE"):
        if preferred in statuses:
            return preferred
    return statuses[0] if statuses else "UNKNOWN"


def _creators_rows_from_items(items: list[dict], include_unavailable: bool = False) -> list[dict]:
    """Extract title, ASIN, parent ASIN and explicit stock status only."""
    rows = []
    checked_at = int(time.time())
    for item in items or []:
        if not isinstance(item, dict):
            continue
        asin = str(item.get("asin") or "").strip().upper()
        title_node = ((item.get("itemInfo") or {}).get("title") or {})
        title = str(title_node.get("displayValue") or "").strip()
        listings = ((item.get("offersV2") or {}).get("listings") or [])
        availability_type = _availability_from_listings(listings)
        if not (title and ASIN_RE.fullmatch(asin)):
            continue
        if not include_unavailable and availability_type not in STOCK_ALLOWED:
            continue
        parent_asin = str(item.get("parentASIN") or asin).strip().upper()
        if not ASIN_RE.fullmatch(parent_asin):
            parent_asin = asin
        rows.append({
            "title": title,
            "asin": asin,
            "parent_asin": parent_asin,
            "availability_type": availability_type,
            "stock_checked_at": checked_at,
            "url": str(item.get("detailPageURL") or ""),
        })
    return rows


def _creators_rows_from_response(data: dict, include_unavailable: bool = False) -> list[dict]:
    """Keep only product identity and stock fields from the official API response."""
    items = (data.get("searchResult") or {}).get("items") or []
    return _creators_rows_from_items(items, include_unavailable=include_unavailable)


def _creators_get_items_by_asin(asins: list[str]) -> dict[str, dict]:
    """Bulk-check up to 10 ASINs per official GetItems request, including stock and parent ASIN."""
    unique = []
    seen = set()
    for value in asins or []:
        asin = str(value or "").strip().upper()
        if ASIN_RE.fullmatch(asin) and asin not in seen:
            seen.add(asin)
            unique.append(asin)
    if not unique:
        return {}

    token = _creators_access_token()
    partner_tag, marketplace = _creators_partner_settings()
    found: dict[str, dict] = {}
    for offset in range(0, len(unique), 10):
        batch = unique[offset:offset + 10]
        payload = {
            "itemIds": batch,
            "itemIdType": "ASIN",
            "partnerTag": partner_tag,
            "marketplace": marketplace,
            "condition": "New",
            "resources": [
                "itemInfo.title",
                "offersV2.listings.availability",
                "offersV2.listings.condition",
                "parentASIN",
            ],
        }
        req = request.Request(
            CREATORS_GET_ITEMS_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-marketplace": marketplace,
                "User-Agent": "EasyLifeHubProductResolver/5.0",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise ProductResolutionError(f"Creators API GetItems HTTP {exc.code}: {detail}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProductResolutionError(f"Creators API GetItems request failed: {exc}") from exc
        if not isinstance(data, dict):
            raise ProductResolutionError("Creators API GetItems returned a non-object response")
        result = data.get("itemsResult") or {}
        rows = _creators_rows_from_items(result.get("items") or [], include_unavailable=True)
        for row in rows:
            found[row["asin"]] = row
        if data.get("errors") and not rows:
            log.warning("Creators API GetItems partial error: %s", str(data.get("errors"))[:300])
    return found


def _search_amazon_creators_api(product_name: str) -> list[dict]:
    """Search for new-condition Amazon offers and retain only explicitly in-stock items."""
    token = _creators_access_token()
    partner_tag, marketplace = _creators_partner_settings()
    payload = {
        "keywords": product_name,
        "partnerTag": partner_tag,
        "marketplace": marketplace,
        "availability": "Available",
        "condition": "New",
        "itemCount": 10,
        "resources": [
            "itemInfo.title",
            "offersV2.listings.availability",
            "offersV2.listings.condition",
            "parentASIN",
        ],
    }
    req = request.Request(
        CREATORS_SEARCH_URL, data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json", "x-marketplace": marketplace,
            "User-Agent": "EasyLifeHubProductResolver/5.0",
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
        raise ProductResolutionError(f"Creators API returned no valid in-stock items: {str(data.get('errors'))[:400]}")
    return rows


def _search_amazon_with_provider(product_name: str, provider: str, api_token: str = "") -> list[dict]:
    """Dispatch one query and enforce Amazon's official in-stock check for every source."""
    if provider == "creators_api":
        return _search_amazon_creators_api(product_name)
    if provider == "serpapi":
        if not api_token:
            raise ProductResolutionError("SERPAPI_API_KEY is missing")
        raw_rows = _search_amazon_serpapi(product_name, api_token)
    elif provider == "apify":
        if not api_token:
            raise ProductResolutionError("TEST_API is missing")
        raw_rows = _search_amazon_apify(product_name, api_token)
    else:
        raise ProductResolutionError(f"Unsupported Amazon product provider: {provider}")

    # A third-party search result is never enough to assert stock. Verify every
    # candidate against Amazon Creators API GetItems before it can become a link.
    stock = _creators_get_items_by_asin([row.get("asin") for row in raw_rows])
    verified = []
    for row in raw_rows:
        asin = _extract_asin(row)
        official = stock.get(asin)
        if not official or official.get("availability_type") not in STOCK_ALLOWED:
            continue
        verified.append({**row, **official})
    return verified


def _search_amazon(product_name: str, api_token: str) -> list[dict]:
    """Dispatch to the configured primary discovery provider."""
    return _search_amazon_with_provider(product_name, _selected_provider(), api_token)

def _stock_check_fresh(item: dict) -> bool:
    try:
        checked_at = float(item.get("stock_checked_at") or 0)
    except (TypeError, ValueError):
        return False
    return checked_at > 0 and 0 <= (time.time() - checked_at) <= STOCK_MAX_AGE_SECONDS


def _trusted_item(item: dict) -> bool:
    asin = str(item.get("asin") or "").strip().upper()
    verified_title = str(item.get("verified_product_title") or "")
    name = str(item.get("display_name") or item.get("name") or "").strip()
    availability = str(item.get("availability_type") or "").strip().upper()
    return (
        item.get("asin_verified") is True
        and bool(ASIN_RE.fullmatch(asin))
        and title_matches(name, verified_title)
        and availability in STOCK_ALLOWED
        and _stock_check_fresh(item)
    )


def _json_string_set(env_name: str) -> set[str]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return set()
    try:
        value = json.loads(raw)
        if isinstance(value, list):
            return {str(v).strip().upper() for v in value if ASIN_RE.fullmatch(str(v).strip().upper())}
    except json.JSONDecodeError:
        pass
    return {
        part.strip().upper()
        for part in raw.split(",")
        if ASIN_RE.fullmatch(part.strip().upper())
    }


def _load_product_registry(
    exclude_slug: str | None = None,
    reserved_asins: set[str] | None = None,
    reserved_parent_asins: set[str] | None = None,
) -> tuple[set[str], set[str]]:
    """Collect product identities already used by other queued/live articles."""
    used_asins = {str(v).strip().upper() for v in (reserved_asins or set())}
    used_parents = {str(v).strip().upper() for v in (reserved_parent_asins or set())}
    if reserved_asins is not None or reserved_parent_asins is not None:
        return used_asins, used_parents

    used_asins.update(_json_string_set("EXTERNAL_ASINS_JSON"))
    used_parents.update(_json_string_set("EXTERNAL_PARENT_ASINS_JSON"))
    try:
        from util import QUEUE_DIR
        for meta_path in QUEUE_DIR.glob("*.json"):
            if meta_path.name == "index.json":
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if exclude_slug and str(meta.get("slug") or "") == exclude_slug:
                continue
            for item in meta.get("items") or []:
                if not isinstance(item, dict):
                    continue
                asin = str(item.get("asin") or "").strip().upper()
                parent = str(item.get("parent_asin") or item.get("parentASIN") or asin).strip().upper()
                if ASIN_RE.fullmatch(asin):
                    used_asins.add(asin)
                if ASIN_RE.fullmatch(parent):
                    used_parents.add(parent)
    except OSError:
        pass
    return used_asins, used_parents


def _apply_candidate(
    item: dict,
    rows: list[dict],
    allow_missing_leading_brand: bool = False,
    used_asins: set[str] | None = None,
    used_parent_asins: set[str] | None = None,
) -> dict | None:
    """Accept only an exact title match, explicit in-stock offer and unused ASIN family."""
    name = str(item.get("display_name") or item.get("name") or "").strip()
    wanted = _normal_tokens(name)
    raw_name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
    raw_tokens = [
        token.lower()
        for token in TOKEN_RE.findall(raw_name.lower())
        if token.lower() not in {"a", "an", "and", "the", "for", "with", "of", "to", "from", "best", "buying", "guide"}
    ]
    leading_token = raw_tokens[0] if raw_tokens else ""
    used_asins = used_asins if used_asins is not None else set()
    used_parent_asins = used_parent_asins if used_parent_asins is not None else set()

    for row in rows:
        title = str(row.get("title") or "").strip()
        asin = _extract_asin(row)
        parent_asin = str(row.get("parent_asin") or asin).strip().upper()
        availability = str(row.get("availability_type") or "").strip().upper()
        if not (
            asin and title and availability in STOCK_ALLOWED
            and _stock_check_fresh(row)
            and asin not in used_asins
            and parent_asin not in used_parent_asins
        ):
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
            result["parent_asin"] = parent_asin
            result["asin_verified"] = True
            result["verified_product_title"] = title
            result["availability_type"] = availability
            result["stock_checked_at"] = int(row.get("stock_checked_at") or time.time())
            result["url"] = str(row.get("url") or "")
            return result
    return None


def resolve_items(
    items: list[dict],
    api_token: str | None = None,
    cache: dict | None = None,
    allow_unresolved: bool = False,
    exclude_slug: str | None = None,
    reserved_asins: set[str] | None = None,
    reserved_parent_asins: set[str] | None = None,
    force_refresh: bool = False,
) -> list[dict]:
    """Resolve products to fresh in-stock Amazon offers without reusing ASIN families."""
    provider = _selected_provider()
    api_token = (api_token if api_token is not None else _provider_api_key(provider)).strip()
    cache = cache if cache is not None else _load_cache()
    used_asins, used_parents = _load_product_registry(
        exclude_slug=exclude_slug,
        reserved_asins=reserved_asins,
        reserved_parent_asins=reserved_parent_asins,
    )
    resolved = [dict(item) for item in items]
    errors = []
    pending: dict[str, list[int]] = {}

    for index, item in enumerate(resolved):
        name = str(item.get("display_name") or item.get("name") or "").strip()
        if not name:
            errors.append("product item has no name")
            continue

        asin = str(item.get("asin") or "").strip().upper()
        parent = str(item.get("parent_asin") or asin).strip().upper()
        duplicate = bool(asin and (asin in used_asins or parent in used_parents))
        if _trusted_item(item) and not duplicate:
            used_asins.add(asin)
            used_parents.add(parent)
            continue

        cache_key = _normal_name(name)
        cached = cache.get(cache_key)
        cached_asin = str(cached.get("asin") or "").strip().upper() if isinstance(cached, dict) else ""
        cached_parent = str(cached.get("parent_asin") or cached_asin).strip().upper() if isinstance(cached, dict) else ""
        cache_fresh = (
            isinstance(cached, dict)
            and bool(ASIN_RE.fullmatch(cached_asin))
            and title_matches(name, str(cached.get("title") or ""))
            and str(cached.get("availability_type") or "").upper() in STOCK_ALLOWED
            and _stock_check_fresh(cached)
            and cached_asin not in used_asins
            and cached_parent not in used_parents
        )
        if cache_fresh and not force_refresh:
            resolved[index].update({
                "asin": cached_asin,
                "parent_asin": cached_parent,
                "asin_verified": True,
                "verified_product_title": str(cached["title"]),
                "availability_type": str(cached["availability_type"]).upper(),
                "stock_checked_at": int(cached["stock_checked_at"]),
            })
            used_asins.add(cached_asin)
            used_parents.add(cached_parent)
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
            candidate = _apply_candidate(
                resolved[index],
                rows,
                allow_missing_leading_brand=(source == "serpapi"),
                used_asins=used_asins,
                used_parent_asins=used_parents,
            )
            if candidate is None:
                remaining.append(index)
                continue
            resolved[index] = candidate
            used_asins.add(candidate["asin"])
            used_parents.add(candidate.get("parent_asin") or candidate["asin"])
            cache[_normal_name(str(candidate.get("display_name") or candidate.get("name") or ""))] = {
                "asin": candidate["asin"],
                "parent_asin": candidate.get("parent_asin") or candidate["asin"],
                "title": candidate["verified_product_title"],
                "availability_type": candidate["availability_type"],
                "stock_checked_at": candidate["stock_checked_at"],
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        return remaining

    for search_term, indexes in pending.items():
        primary_error = None
        unresolved = list(indexes)
        try:
            if not api_token:
                raise ProductResolutionError(f"{_provider_secret_name(provider)} is missing")
            primary_rows = _search_amazon(search_term, api_token)
            unresolved = apply_rows(primary_rows, provider, unresolved)
        except ProductResolutionError as exc:
            primary_error = exc
            log.warning("Amazon primary search failed | %s | %s", search_term, exc)

        if unresolved and primary_error and _is_global_provider_error(primary_error) and not fallback_providers:
            raise primary_error

        fallback_errors = []
        for fallback_provider in fallback_providers:
            if not unresolved:
                break
            fallback_key = _provider_api_key(fallback_provider)
            try:
                fallback_rows = _search_amazon_with_provider(search_term, fallback_provider, fallback_key)
            except ProductResolutionError as exc:
                fallback_errors.append(str(exc))
                log.warning("Amazon fallback search failed | provider=%s | %s", fallback_provider, exc)
                if _is_global_provider_error(exc):
                    raise
                continue
            unresolved = apply_rows(fallback_rows, fallback_provider, unresolved)

        if unresolved:
            for index in unresolved:
                item = resolved[index]
                name = item.get("display_name") or item.get("name")
                if primary_error:
                    message = f"{primary_error}; no verified in-stock fallback match for: {name}"
                elif fallback_errors:
                    message = f"{'; '.join(fallback_errors[:2])}; no unique in-stock match for: {name}"
                else:
                    message = f"no unique verified in-stock Amazon match for: {name}"
                errors.append(message)
                log.warning("product resolution failed | %s", message)
                # Never leave an old/unverified link in a result that the caller
                # explicitly requested with allow_unresolved=True.
                item["asin_verified"] = False
                item["url"] = ""
                item.pop("asin", None)
                item.pop("parent_asin", None)
                item.pop("verified_product_title", None)
                item.pop("availability_type", None)
                item.pop("stock_checked_at", None)
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
    topic["items"] = resolve_items(items, exclude_slug=str(topic.get("slug") or ""))
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


def _disable_amazon_link(body: str, url: str) -> str:
    """Turn every CTA for an unverified product into non-clickable text."""
    if not url:
        return body
    variants = {url, html_lib.escape(url, quote=True)}
    for candidate in variants:
        pattern = re.compile(
            r"<a\b(?=[^>]*\bhref\s*=\s*(['\"])" + re.escape(candidate) +
            r"\1)[^>]*>([\s\S]*?)</a\s*>",
            re.I,
        )
        body = pattern.sub(
            lambda match: '<span class="amazon-unavailable">' + match.group(2) + "</span>",
            body,
        )
    return body


def _published_priority() -> list[str]:
    raw = os.environ.get("PUBLISHED_SLUGS_JSON", "").strip()
    if not raw:
        return []
    try:
        values = json.loads(raw)
        return [str(v) for v in values if isinstance(v, str)]
    except json.JSONDecodeError:
        log.warning("PUBLISHED_SLUGS_JSON is invalid; using generation order for article priority")
        return []


def audit_queue_products() -> dict:
    """Recheck stock, remove duplicate ASIN families, and rewrite every queued article's CTAs."""
    from affiliate import Affiliate
    from util import QUEUE_DIR, load_config, load_state
    from render import rebuild_index

    cfg = load_config()
    aff = Affiliate(cfg)
    files = []
    metadata_by_path = {}
    for meta_path in QUEUE_DIR.glob("*.json"):
        if meta_path.name in {"index.json", "product-audit.json"}:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict) or not (meta.get("items") or []):
            continue
        files.append(meta_path)
        metadata_by_path[meta_path] = meta

    if not _provider_api_key("creators_api"):
        raise ProductResolutionError(
            "AMAZON_CREATORS_CLIENT_ID and AMAZON_CREATORS_CLIENT_SECRET are required for stock auditing"
        )

    # Audit previously published articles first so their product links win over
    # queued drafts. Within each group, preserve the oldest generation order.
    state = load_state()
    generated = {
        str(row.get("slug") or ""): str(row.get("ts") or "")
        for row in (state.get("generated") or [])
        if isinstance(row, dict)
    }
    published_order = _published_priority()
    published_rank = {slug: index for index, slug in enumerate(published_order)}
    files.sort(key=lambda path: (
        0 if str(metadata_by_path[path].get("slug") or "") in published_rank else 1,
        published_rank.get(str(metadata_by_path[path].get("slug") or ""), 10**9),
        generated.get(str(metadata_by_path[path].get("slug") or ""), "9999"),
        str(metadata_by_path[path].get("slug") or path.stem),
    ))

    external_asins = sorted(_json_string_set("EXTERNAL_ASINS_JSON"))
    all_existing_asins = set(external_asins)
    for meta in metadata_by_path.values():
        for item in meta.get("items") or []:
            asin = str(item.get("asin") or "").strip().upper()
            if ASIN_RE.fullmatch(asin):
                all_existing_asins.add(asin)

    # One official GetItems request can verify up to ten ASINs. This is much
    # cheaper than running a new keyword search for every existing product.
    stock_by_asin = _creators_get_items_by_asin(sorted(all_existing_asins))
    used_asins = set(external_asins)
    used_parents = set()
    external_products = {}
    for asin in external_asins:
        row = stock_by_asin.get(asin) or {}
        parent = str(row.get("parent_asin") or asin).upper()
        external_products[asin] = {
            "parent_asin": parent,
            "availability_type": str(row.get("availability_type") or "UNKNOWN"),
            "stock_checked_at": int(row.get("stock_checked_at") or time.time()),
        }
        if ASIN_RE.fullmatch(parent):
            used_parents.add(parent)

    cache = _load_cache()
    updated_articles = 0
    replacements = 0
    unresolved_items = 0
    kept_existing = 0
    duplicate_or_oos = 0
    now = int(time.time())

    for meta_path in files:
        meta = metadata_by_path[meta_path]
        slug = str(meta.get("slug") or meta_path.stem)
        old_items = [dict(item) for item in (meta.get("items") or []) if isinstance(item, dict)]
        new_items = []
        old_urls = []

        for old_item in old_items:
            old_url = str(old_item.get("url") or aff.url_for(old_item) or "")
            old_urls.append(old_url)
            old_asin = str(old_item.get("asin") or "").strip().upper()
            official = stock_by_asin.get(old_asin) if old_asin else None
            name = str(old_item.get("display_name") or old_item.get("name") or "").strip()
            parent = str((official or {}).get("parent_asin") or old_item.get("parent_asin") or old_asin).strip().upper()

            candidate = None
            can_keep = (
                bool(official)
                and official.get("availability_type") in STOCK_ALLOWED
                and title_matches(name, str(official.get("title") or ""))
                and old_asin not in used_asins
                and parent not in used_parents
            )
            if can_keep:
                candidate = dict(old_item)
                candidate.update({
                    "asin": old_asin,
                    "parent_asin": parent or old_asin,
                    "asin_verified": True,
                    "verified_product_title": official["title"],
                    "availability_type": official["availability_type"],
                    "stock_checked_at": official["stock_checked_at"],
                })
                kept_existing += 1
            else:
                if old_asin:
                    duplicate_or_oos += 1
                query = str(old_item.get("search_term") or name).strip()
                rows = []
                try:
                    rows = _search_amazon_creators_api(query)
                except ProductResolutionError as exc:
                    log.warning("Creators API replacement search failed | %s | %s", query, exc)
                    for fallback in _fallback_provider_names("creators_api"):
                        if not (_provider_spending_enabled(fallback) and _provider_api_key(fallback)):
                            continue
                        try:
                            rows = _search_amazon_with_provider(query, fallback, _provider_api_key(fallback))
                            break
                        except ProductResolutionError as fallback_exc:
                            log.warning("product fallback failed | %s | %s", fallback, fallback_exc)
                candidate = _apply_candidate(
                    old_item,
                    rows,
                    used_asins=used_asins,
                    used_parent_asins=used_parents,
                )
                if candidate is None:
                    for fallback in _fallback_provider_names("creators_api"):
                        if not (_provider_spending_enabled(fallback) and _provider_api_key(fallback)):
                            continue
                        try:
                            fallback_rows = _search_amazon_with_provider(
                                query, fallback, _provider_api_key(fallback)
                            )
                            candidate = _apply_candidate(
                                old_item,
                                fallback_rows,
                                allow_missing_leading_brand=(fallback == "serpapi"),
                                used_asins=used_asins,
                                used_parent_asins=used_parents,
                            )
                            if candidate:
                                break
                        except ProductResolutionError as fallback_exc:
                            log.warning("product fallback failed | %s | %s", fallback, fallback_exc)
                if candidate:
                    replacements += 1

            if candidate:
                asin = str(candidate["asin"]).upper()
                parent_asin = str(candidate.get("parent_asin") or asin).upper()
                candidate["url"] = aff.url_for(candidate)
                used_asins.add(asin)
                used_parents.add(parent_asin)
                cache[_normal_name(name)] = {
                    "asin": asin,
                    "parent_asin": parent_asin,
                    "title": candidate["verified_product_title"],
                    "availability_type": candidate["availability_type"],
                    "stock_checked_at": candidate["stock_checked_at"],
                    "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            else:
                unresolved_items += 1
                candidate = dict(old_item)
                candidate["asin_verified"] = False
                candidate["availability_type"] = str((official or {}).get("availability_type") or "UNKNOWN")
                candidate["stock_checked_at"] = int((official or {}).get("stock_checked_at") or now)
                candidate["parent_asin"] = parent or old_asin
                candidate["url"] = ""
                if old_asin:
                    used_asins.add(old_asin)
                if parent and ASIN_RE.fullmatch(parent):
                    used_parents.add(parent)
            new_items.append(candidate)

        all_valid = bool(new_items) and all(_trusted_item(item) for item in new_items)
        html_path = QUEUE_DIR / str(meta.get("html_file") or f"{slug}.html")
        if html_path.exists():
            body = html_path.read_text(encoding="utf-8")
            for old_item, new_item, old_url in zip(old_items, new_items, old_urls):
                new_url = aff.url_for(new_item) if _trusted_item(new_item) else ""
                if old_url and new_url:
                    body = body.replace(html_lib.escape(old_url, quote=True), html_lib.escape(new_url, quote=True))
                    body = body.replace(old_url, html_lib.escape(new_url, quote=True))
                elif old_url:
                    body = _disable_amazon_link(body, old_url)
            html_path.write_text(body, encoding="utf-8")
            md_path = html_path.with_suffix(".md")
            if md_path.exists():
                markdown = md_path.read_text(encoding="utf-8")
                for old_url, new_item in zip(old_urls, new_items):
                    new_url = aff.url_for(new_item) if _trusted_item(new_item) else ""
                    if old_url and new_url:
                        markdown = markdown.replace(old_url, new_url)
                    elif old_url:
                        markdown = markdown.replace(f"]({old_url})", "]")
                md_path.write_text(markdown, encoding="utf-8")

        meta["items"] = new_items
        meta["product_links_verified"] = all_valid
        meta["review_required"] = bool(meta.get("review_required", False)) or not all_valid
        meta["products_audited_at"] = now
        meta["stock_check_max_age_seconds"] = STOCK_MAX_AGE_SECONDS
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        updated_articles += 1

    audit_report = {
        "checked_at": now,
        "stock_check_max_age_seconds": STOCK_MAX_AGE_SECONDS,
        "articles_checked": updated_articles,
        "external_products": external_products,
        "external_asins": sorted(external_asins),
        "stock_by_asin": {
            asin: {
                "title": row.get("title", ""),
                "parent_asin": row.get("parent_asin") or asin,
                "availability_type": row.get("availability_type", "UNKNOWN"),
                "stock_checked_at": int(row.get("stock_checked_at") or now),
            }
            for asin, row in stock_by_asin.items()
        },
        "unique_queue_asins": len(used_asins - set(external_asins)),
        "unresolved_items": unresolved_items,
        "replacements": replacements,
        "kept_existing_in_stock": kept_existing,
        "duplicate_or_out_of_stock_encountered": duplicate_or_oos,
    }
    (QUEUE_DIR / "product-audit.json").write_text(
        json.dumps(audit_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rebuild_index(cfg)
    _save_cache(cache)
    log.info(
        "Amazon product audit complete: articles=%d kept=%d replaced=%d unresolved=%d",
        updated_articles, kept_existing, replacements, unresolved_items,
    )
    return audit_report


def backfill_queue() -> dict:
    """Compatibility entry point for the existing backfill workflow."""
    return audit_queue_products()
