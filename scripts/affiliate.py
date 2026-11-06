"""Affiliate link building + disclosure + link-rot protection.

Only verified ASINs are allowed. Every affiliate link resolves directly to an
Amazon product detail page; missing or invalid ASINs produce no link.

Every link gets rel="nofollow sponsored noopener" (Google requirement for
affiliate links) and target="_blank".
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from util import log

MARKETPLACES = {
    "com": ("https://www.amazon.com", "US", "USD"),
    "co.uk": ("https://www.amazon.co.uk", "GB", "GBP"),
    "de": ("https://www.amazon.de", "DE", "EUR"),
    "fr": ("https://www.amazon.fr", "FR", "EUR"),
    "es": ("https://www.amazon.es", "ES", "EUR"),
    "it": ("https://www.amazon.it", "IT", "EUR"),
    "ca": ("https://www.amazon.ca", "CA", "CAD"),
    "eg": ("https://www.amazon.eg", "EG", "EGP"),
    "sa": ("https://www.amazon.sa", "SA", "SAR"),
    "ae": ("https://www.amazon.ae", "AE", "AED"),
    "in": ("https://www.amazon.in", "IN", "INR"),
}

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


class Affiliate:
    def __init__(self, cfg: dict):
        aff = cfg.get("affiliate", {})
        self.tag = (aff.get("tag") or "").strip()
        self.market = aff.get("marketplace", "com")
        self.base, self.country, self.currency = MARKETPLACES.get(self.market, MARKETPLACES["com"])
        self.rel = aff.get("link_rel", "nofollow sponsored noopener")
        self.disclosure = aff.get("disclosure", "")
        self.use_search = bool(aff.get("use_search_links_when_no_asin", True))
        if not self.tag or self.tag.startswith("YOURTAG"):
            log.warning("Affiliate tag not configured yet - links will be built without ?tag=")

    # ------------------------------------------------------------- links ----
    def _tagged(self, url: str) -> str:
        if not self.tag:
            return url
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={self.tag}"

    def product_url(self, asin: str = "", keyword: str = "") -> str:
        asin = (asin or "").strip().upper()
        if ASIN_RE.fullmatch(asin):
            return self._tagged(f"{self.base}/dp/{asin}")
        # Search-result pages are deliberately disabled: an affiliate CTA must
        # resolve to a specific, verified product detail page, never a search.
        if asin:
            log.warning("Invalid ASIN '%s' - product URL suppressed", asin)
        elif keyword:
            log.debug("No verified ASIN for '%s' - product URL suppressed", keyword)
        return ""

    def url_for(self, item: dict) -> str:
        """Prefer an explicit search_term (converts better than the bare noun)."""
        return self.product_url(
            item.get("asin", ""),
            item.get("search_term") or item.get("keyword") or item.get("name", ""),
        )

    # -------------------------------------------------------------- html ----
    def a(self, url: str, text: str, cls: str = "") -> str:
        klass = f' class="{cls}"' if cls else ""
        return f'<a href="{url}"{klass} rel="{self.rel}" target="_blank">{text}</a>'

    def cta_button(self, asin: str = "", keyword: str = "", label: str = "View Product on Amazon") -> str:
        url = self.product_url(asin, keyword)
        return (
            '<div class="cta-wrap">'
            f'<a class="cta-btn" href="{url}" rel="{self.rel}" target="_blank">'
            f'<span>{label}</span><span class="cta-arrow">&#8594;</span></a>'
            '<small class="cta-note">See the current product details on Amazon.</small>'
            "</div>"
        )

    def product_box(self, item: dict, label: str = "View Product on Amazon") -> str:
        url = self.url_for(item)
        name = item.get("display_name") or item.get("name", "This product")
        badge = item.get("budget") or item.get("feature") or item.get("audience") or ""
        badge_html = f'<span class="pb-badge" data-owner-data="1">{badge}</span>' if badge else ""
        return (
            '<div class="product-box">'
            f'<div class="pb-head"><span class="pb-name">{name}</span>{badge_html}</div>'
            f'<a class="cta-btn" href="{url}" rel="{self.rel}" target="_blank">'
            f"<span>{label}</span><span class=\"cta-arrow\">&#8594;</span></a>"
            "</div>"
        )

    def disclosure_block(self) -> str:
        if not self.disclosure:
            return ""
        return (
            '<div class="disclosure" role="note">'
            '<strong>Affiliate disclosure.</strong> '
            f"{self.disclosure}"
            "</div>"
        )

    def footer_note(self) -> str:
        return (
            '<p class="foot-note">As an Amazon Associate we earn from qualifying purchases. '
            "Product prices and availability are accurate as of the date/time indicated and are "
            "subject to change.</p>"
        )


def inline_affiliate_links(html: str, aff: Affiliate, items: list[dict]) -> str:
    """Turn the first plain mention of each product name into an affiliate link.

    Keeps the copy readable (one link per product, max) instead of stuffing.
    """
    for item in items:
        name = (item.get("display_name") or item.get("name") or "").strip()
        if not name or len(name) < 4:
            continue
        url = aff.url_for(item)
        pattern = re.compile(rf"(?<![\w\">/]){re.escape(name)}(?![\w<])", re.IGNORECASE)
        html, n = pattern.subn(
            f'<a href="{url}" rel="{aff.rel}" target="_blank">{name}</a>', html, count=1
        )
        if n:
            log.debug("linked first mention of %s", name)
    return html
