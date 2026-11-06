"""Safety layer - the reason this system does not get your Blogger account banned.

Blogger runs automated spam detection on API posting, and accounts get their
API access restricted (sometimes permanently, with no reply to review requests)
when posting looks machine-made. Three defences:

  1. Quality gate   - thin / hype / fabricated-content posts never leave the box
  2. Circuit breaker - after N consecutive failures the whole system pauses and
                      waits for a human, instead of hammering the API
  3. Anti-fingerprint - randomised delays, hard daily cap, min gap between posts
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timedelta, timezone

from ai_writer import BANNED_PHRASES, HYPE_RE, PRICE_RE, RATING_RE, CUSTOMER_REVIEW_RE
from util import load_state, log, save_state, word_count

EXCESSIVE_EXCLAMATION = re.compile(r"!{2,}")
ALLCAPS_WORDS = re.compile(r"\b[A-Z]{6,}\b")
LINK_SPAM_THRESHOLD = 25          # affiliate links per post
REPEATED_SENTENCE_LIMIT = 3
TABLE_RE = re.compile(r"<table\b.*?</table>", re.S | re.I)
TAG_OPEN_CLOSE_RE = re.compile(r"</?[a-zA-Z][^>]*>")
ENTITY_FRAGMENT_RE = re.compile(r"&?[a-zA-Z]+;")
# Owner-supplied data (budget badges, spec cells) is legitimate - it came from
# data/products.csv, not from a language model. Anything else with a currency
# symbol in body copy is a hallucinated price and gets rejected.
OWNER_DATA_RE = re.compile(r'<[^>]+data-owner-data[^>]*>.*?</[^>]+>', re.S | re.I)


def _prose_only(html: str) -> str:
    """Body copy with tables and markup removed.

    Numbers inside a spec table are data the *owner* supplied (products.csv),
    so they are legitimate. Numbers invented inside a sentence are not - that
    is what the fabricated-price check is for.

    Tags are stripped with an anchored pattern so HTML entities survive intact
    (&pound;149 must not degrade into "pound;149" and trip the price regex).
    """
    text = TABLE_RE.sub(" ", html)
    text = OWNER_DATA_RE.sub(" ", text)
    text = TAG_OPEN_CLOSE_RE.sub(" ", text)
    text = ENTITY_FRAGMENT_RE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text)


# ------------------------------------------------------------ quality gate ---
def quality_gate(html: str, article: dict, cfg: dict, expected_product_count: int | None = None) -> tuple[bool, list[str]]:
    """Final pre-publish check on the rendered HTML. (ok, reasons)."""
    reasons: list[str] = []
    min_words = int(cfg.get("content", {}).get("min_words", 1100))

    words = word_count(html)
    if words < min_words:
        reasons.append(f"thin content: {words} words (min {min_words})")

    prose = _prose_only(html)
    prose_lower = prose.lower()
    for phrase in BANNED_PHRASES:
        if phrase in prose_lower:
            reasons.append(f"banned phrase survived rendering: {phrase}")
    if HYPE_RE.search(prose_lower):
        reasons.append("hype language survived rendering")
    if PRICE_RE.search(prose):
        reasons.append("fabricated price in body copy (numbers belong in the spec table only)")

    # Every hyperlink in a post must point to Amazon and carry the configured
    # Associates tracking tag. Images and plain text are not treated as links.
    expected_tag = str(cfg.get("affiliate", {}).get("tag") or "").strip()
    anchors = re.findall(r"<a\b([^>]*)>", html, re.I | re.S)
    link_count = 0
    linked_asins: set[str] = set()
    for attrs in anchors:
        href_match = re.search(r""" \bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)) """.strip(), attrs, re.I | re.S)
        if not href_match:
            reasons.append("anchor without href")
            continue
        href = unescape(next((part for part in href_match.groups() if part is not None), "")).strip()
        try:
            parsed = urlsplit(href)
            host = (parsed.hostname or "").lower()
        except ValueError:
            parsed = None
            host = ""
        if host != "amazon.com" and not host.endswith(".amazon.com"):
            reasons.append("non-Amazon hyperlink found")
            continue
        link_count += 1
        path_match = re.fullmatch(r"/dp/([A-Z0-9]{10})/?", parsed.path, re.I) if parsed else None
        if not path_match:
            reasons.append("Amazon search or non-product URL found; direct /dp/ASIN links are required")
        else:
            linked_asins.add(path_match.group(1).upper())
        tags = parse_qs(parsed.query).get("tag", []) if parsed else []
        if not expected_tag or expected_tag not in tags:
            reasons.append("Amazon link missing the configured Associates tracking tag")
    if expected_product_count is not None and len(linked_asins) < int(expected_product_count):
        reasons.append(
            f"missing direct product links: {len(linked_asins)} of {int(expected_product_count)} products"
        )
    if link_count > LINK_SPAM_THRESHOLD:
        reasons.append(f"link stuffing: {link_count} affiliate links")

    if EXCESSIVE_EXCLAMATION.search(html):
        reasons.append("excessive punctuation")

    # repeated-sentence detector (a classic AI tell)
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", prose_lower) if len(x.strip()) > 40]
    seen: dict[str, int] = {}
    for sentence in sentences:
        seen[sentence] = seen.get(sentence, 0) + 1
    for sentence, count in seen.items():
        if count > REPEATED_SENTENCE_LIMIT:
            reasons.append(f"repeated sentence x{count}: {sentence[:60]}...")
            break

    if "affiliate disclosure" not in prose_lower and "as an amazon associate" not in prose_lower:
        reasons.append("missing affiliate disclosure (Amazon ToS violation)")

    if "<h2" not in html:
        reasons.append("no h2 headings - poor structure")

    if "tag=YOURTAG" in html:
        # warning only: a fresh install must still be able to generate
        log.warning("  placeholder affiliate tag detected - these links earn nothing")

    if ALLCAPS_WORDS.search(re.sub(r"<[^>]+>", " ", html)):
        log.debug("all-caps words present (allowed, logged only)")

    return (not reasons), reasons


# ---------------------------------------------------------- circuit breaker --
def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def breaker_closed(cfg: dict) -> tuple[bool, str]:
    """True => do NOT publish. Returns (closed, reason)."""
    state = load_state()
    failures = state.get("failures", {})
    until = _parse_iso(failures.get("until"))
    if until and datetime.now(timezone.utc) < until:
        remaining = (until - datetime.now(timezone.utc)).seconds // 60
        return True, (
            f"circuit breaker OPEN after {failures.get('count')} consecutive failures "
            f"(~{remaining} min left). Last error: {str(failures.get('last_error'))[:180]}"
        )
    return False, ""


def record_success(state: dict) -> None:
    state.setdefault("failures", {})["count"] = 0
    state["failures"]["until"] = None
    state["failures"]["last_error"] = None
    state["last_publish_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["counters"]["total_published"] = state["counters"].get("total_published", 0) + 1
    save_state(state)


def record_failure(cfg: dict, error: str) -> None:
    state = load_state()
    failures = state.setdefault("failures", {"count": 0, "until": None, "last_error": None})
    failures["count"] = int(failures.get("count", 0)) + 1
    failures["last_error"] = error[:500]
    limit = int(cfg.get("safety", {}).get("max_consecutive_failures", 3))
    if failures["count"] >= limit:
        hours = int(cfg.get("safety", {}).get("cooldown_hours", 24))
        failures["until"] = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        log.error("CIRCUIT BREAKER OPEN for %sh after %d failures", hours, failures["count"])
    save_state(state)


# --------------------------------------------------------- anti-fingerprint --
def rate_ok(cfg: dict, planned: int = 1) -> tuple[bool, str]:
    """Hard caps that protect the account even if the config is edited badly."""
    safety = cfg.get("safety", {})
    state = load_state()
    cap = int(safety.get("max_posts_per_day_hard_cap", 6))
    published_today = sum(
        1 for p in state.get("published", []) if str(p.get("ts", "")).startswith(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    )
    if published_today + planned > cap:
        return False, f"hard cap reached ({published_today}/{cap} published today)"

    min_gap = int(safety.get("min_seconds_between_posts", 7200))
    last = _parse_iso(state.get("last_publish_ts"))
    if last:
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < min_gap:
            return False, f"too soon after last post ({int(elapsed)}s < {min_gap}s)"
    return True, ""
