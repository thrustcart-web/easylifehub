"""Hero images - free, legal, and never broken.

Amazon forbids hotlinking their CDN images, and AI image generation is not free.
So the pipeline uses, in order:

  1. Unsplash API  (free, 50 requests/hour on the demo tier, requires a free key)
     -> results are cached in .cache/images.json and committed to the repo,
        so the same keyword never burns quota twice.
  2. A locally generated SVG card (no network, no key, always works).

The SVG fallback is what makes the "set up and forget" promise hold: even with
zero API keys the post still goes out looking intentional, not broken.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from util import ROOT, log

UNSPLASH_API = "https://api.unsplash.com/search/photos"
PALETTES = [
    ("#0f2027", "#2c5364"),
    ("#232526", "#414345"),
    ("#1f4037", "#99f2c8"),
    ("#3a1c71", "#d76d77"),
    ("#141e30", "#243b55"),
    ("#0b486b", "#f56217"),
    ("#2b5876", "#4e4376"),
    ("#134e5e", "#71b280"),
]


def _cache_path(cfg: dict) -> "ROOT":
    rel = cfg.get("cache_file", ".cache/images.json")
    return ROOT / rel


def _load_cache(cfg: dict) -> dict:
    path = _cache_path(cfg)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cfg: dict, cache: dict) -> None:
    path = _cache_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------- svg ----
def svg_card(title: str, subtitle: str = "Hands-on buyer's guide") -> str:
    """Self-contained SVG (data-uri friendly, renders inside Blogger posts)."""
    seed = int(hashlib.md5(title.encode()).hexdigest()[:8], 16)
    c1, c2 = PALETTES[seed % len(PALETTES)]
    words = re.findall(r"[A-Za-z0-9']+", title)[:7]
    lines, cur = [], ""
    for word in words:
        if len(cur) + len(word) + 1 > 30:
            lines.append(cur.strip())
            cur = word
        else:
            cur += " " + word
    if cur.strip():
        lines.append(cur.strip())
    lines = lines[:3] or ["Review"]

    tspans = "".join(
        f'<tspan x="600" dy="{0 if i == 0 else 62}">{_esc(line.title())}</tspan>'
        for i, line in enumerate(lines)
    )
    y_start = 300 - (len(lines) - 1) * 31
    circles = "".join(
        f'<circle cx="{(seed >> (i * 3)) % 1100 + 50}" cy="{(seed >> (i * 5)) % 500 + 60}" '
        f'r="{40 + (seed >> i) % 90}" fill="#ffffff" opacity="0.05"/>'
        for i in range(5)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" width="1200" height="630" role="img" aria-label="{_esc(title)}">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0%" stop-color="{c1}"/><stop offset="100%" stop-color="{c2}"/></linearGradient></defs>
<rect width="1200" height="630" fill="url(#g)"/>{circles}
<rect x="60" y="520" width="90" height="6" rx="3" fill="#ffffff" opacity="0.85"/>
<text x="600" y="{y_start}" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif"
      font-size="54" font-weight="700" fill="#ffffff">{tspans}</text>
<text x="600" y="560" text-anchor="middle" font-family="Helvetica, Arial, sans-serif"
      font-size="24" letter-spacing="4" fill="#ffffff" opacity="0.8">{_esc(subtitle.upper())}</text>
</svg>'''


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------- unsplash ---
def _unsplash_fetch(keyword: str, key: str) -> dict | None:
    params = urlparse.urlencode(
        {"query": keyword, "per_page": 1, "orientation": "landscape", "content_filter": "high"}
    )
    req = urlrequest.Request(
        f"{UNSPLASH_API}?{params}",
        headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
    )
    try:
        with urlrequest.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urlerror.HTTPError, urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.warning("Unsplash lookup failed for '%s': %s", keyword, str(exc)[:120])
        return None
    results = data.get("results") or []
    if not results:
        return None
    photo = results[0]
    user = photo.get("user") or {}
    return {
        "url": (photo.get("urls") or {}).get("regular"),
        "alt": photo.get("alt_description") or keyword,
        "photographer": user.get("name", "Unsplash"),
        "photographer_url": user.get("links", {}).get("html", "https://unsplash.com"),
        "photo_url": photo.get("links", {}).get("html", "https://unsplash.com"),
        "provider": "unsplash",
    }


# ---------------------------------------------------------------- public ----
def hero_html(keyword: str, title: str, cfg: dict, niche: str = "") -> str:
    """Return a ready-to-paste hero block (cached, with attribution)."""
    cache = _load_cache(cfg)
    key = keyword.lower().strip()

    if key not in cache:
        api_key = cfg.get("access_key", "")
        record = None
        if api_key and not api_key.startswith("PUT_YOUR"):
            record = _unsplash_fetch(keyword, api_key) or (
                _unsplash_fetch(niche, api_key) if niche else None
            )
        if not record:
            record = {"provider": "svg"}
        cache[key] = record
        _save_cache(cfg, cache)

    record = cache[key]
    if record.get("provider") == "unsplash" and record.get("url"):
        credit = ""
        if cfg.get("attribution", True):
            credit = (
                f'<div class="img-credit">Photo by '
                f'<a href="{record["photographer_url"]}?utm_source=autoblog&utm_medium=referral" '
                f'rel="noopener" target="_blank">{_esc(record["photographer"])}</a> on '
                f'<a href="https://unsplash.com/?utm_source=autoblog&utm_medium=referral" rel="noopener" '
                f'target="_blank">Unsplash</a></div>'
            )
        return (
            f'<figure class="hero"><img src="{record["url"]}" alt="{_esc(record.get("alt") or title)}" '
            f'loading="lazy" width="1080" height="720" style="max-width:100%;height:auto;border-radius:14px;"/>'
            f"{credit}</figure>"
        )

    svg = svg_card(title)
    return f'<figure class="hero hero-svg">{svg}<figcaption class="img-credit">{_esc(title)}</figcaption></figure>'


def social_preview_url(keyword: str, title: str, cfg: dict) -> str:
    """Best-effort Open Graph image URL (unsplash if cached, else empty)."""
    cache = _load_cache(cfg)
    rec = cache.get(keyword.lower().strip(), {})
    return rec.get("url", "")
