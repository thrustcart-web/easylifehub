"""Category hub + in-post category links (topical-authority plumbing).

Three jobs:

1. ``build_hub()`` writes ``content/categories/index.html`` - a "Browse all
   categories" page that Apps Script publishes as a Blogger **Page** (not a
   Post, so it never counts against the daily posting rate).

2. ``chip_html()`` renders the small "More in <niche>" link that goes into
   every article. Every post therefore links out to its category, and every
   category links to every post in it - the internal-link graph Google uses to
   decide whether a site is an authority on a subject or just a pile of pages.

3. ``related_placeholder()`` emits an empty div that Apps Script fills with
   live related posts (same label) at publish time. Related links cannot be
   baked in here: when article #7 is generated, articles #400-#1800 do not
   exist yet.

Links are written relative (``/search/label/Kitchen``). Apps Script rewrites
them to absolute URLs because it is the only component that reliably knows the
final blog address.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from util import ROOT, log, read_csv_rows

KEYWORDS_CSV = ROOT / "data" / "keywords.csv"
QUEUE_DIR = ROOT / "content" / "queue"
STATE_PATH = ROOT / "state.json"

# niche key -> (display label, emoji, one-line shopper-facing description)
NICHES: dict[str, tuple[str, str, str]] = {
    "kitchen": ("Kitchen", "\U0001F373", "Cookware, small appliances and the tools that survive daily use."),
    "fitness": ("Fitness", "\U0001F4AA", "Home gym gear, recovery and everything that gets used more than twice."),
    "electronics": ("Electronics", "\U0001F50C", "Chargers, cables, displays and the gadgets worth the counter space."),
    "outdoor": ("Outdoor", "\u26FA", "Camping, hiking and gear that has to work when the weather does not."),
    "home office": ("Home Office", "\U0001F5A5\uFE0F", "Desks, seating and setups for people who work where they live."),
    "garden": ("Garden", "\U0001F331", "Growing things in beds, pots or a balcony with questionable sunlight."),
    "baby & kids": ("Baby & Kids", "\U0001F476", "Safety-first picks for small humans and the people who chase them."),
    "pets": ("Pets", "\U0001F43E", "Beds, bowls, toys and travel gear for cats and dogs."),
    "home cleaning": ("Home Cleaning", "\U0001F9F9", "Vacuums, mops and the small things that keep a house presentable."),
    "car": ("Car", "\U0001F697", "Accessories, care and emergency kit for the vehicle you actually drive."),
    "coffee & espresso": ("Coffee & Espresso", "\u2615", "Grinders, machines and the pursuit of a better morning cup."),
    "budget tech": ("Budget Tech", "\U0001F4B8", "Cheap hardware that punches above its price - and the stuff that does not."),
    "tools": ("Tools", "\U0001F6E0\uFE0F", "Hand and power tools for repairs, builds and weekend projects."),
    "travel": ("Travel", "\u2708\uFE0F", "Packing, luggage and gear that survives airports and overhead bins."),
    "sleep": ("Sleep", "\U0001F6CF\uFE0F", "Mattresses, pillows and blackout solutions for people who count hours."),
    "personal care": ("Personal Care", "\U0001F9F4", "Grooming, skin and hair tools reviewed by criteria, not by hype."),
    "smart home": ("Smart Home", "\U0001F3E0", "Plugs, sensors and hubs that should make life easier, not more complicated."),
    "audio": ("Audio", "\U0001F3A7", "Headphones, speakers and everything between your ears and the source."),
    # ---- expansion niches (added by tools/expand_keywords.py) ----
    "health & wellness": ("Health & Wellness", "\U0001F9D8", "Monitors, supplements and recovery tools with the claims checked."),
    "office & school supplies": ("Office & School Supplies", "\U0001F4DA", "Paper, pens and desk organisation that actually holds up."),
    "fashion & accessories": ("Fashion & Accessories", "\U0001F45C", "Bags, watches and everyday wear judged on materials and wear."),
    "photography & video": ("Photography & Video", "\U0001F4F7", "Tripods, lighting and storage for people who make the content."),
    "gaming": ("Gaming", "\U0001F3AE", "Controllers, headsets and desk gear for long sessions."),
    "food & pantry": ("Food & Pantry", "\U0001F36F", "Staples, storage and small kitchen purchases worth repeating."),
    "home decor": ("Home Decor", "\U0001F5BC\uFE0F", "Lighting, rugs and the finishing touches that are easy to overbuy."),
    "security & safety": ("Security & Safety", "\U0001F512", "Cameras, locks and detectors - the purchases you hope never matter."),
    "crafts & hobbies": ("Crafts & Hobbies", "\U0001F9F5", "Sewing, art and maker gear for people with too many projects."),
    "laundry & storage": ("Laundry & Storage", "\U0001F9FA", "Washing, drying and keeping a small space organised."),
}

HUB_CSS = """
<style>
.cat-body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1c1f23;line-height:1.7;font-size:17px;max-width:860px}
.cat-body h2{font-size:1.4rem;margin:1.8rem 0 .8rem;font-weight:800;letter-spacing:-.015em}
.cat-body p.lead{color:#5b6472;margin:0 0 1.4rem}
.cat-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin:0 0 1.6rem;padding:0;list-style:none}
.cat-card{border:1px solid #e6e8ec;border-radius:12px;padding:14px 16px;background:#fff;transition:border-color .15s}
.cat-card:hover{border-color:#146c43}
.cat-card a{display:block;text-decoration:none;color:#1c1f23}
.cat-name{font-weight:700;font-size:1.02rem;display:block}
.cat-desc{color:#5b6472;font-size:.9rem;margin-top:4px;display:block;line-height:1.5}
.cat-count{display:inline-block;margin-top:8px;font-size:.78rem;font-weight:700;color:#146c43;background:#e8f5ee;border-radius:20px;padding:2px 10px}
.cat-chip{display:inline-block;margin:0 8px 8px 0;padding:6px 13px;border:1px solid #e6e8ec;border-radius:20px;font-size:.88rem;text-decoration:none;color:#0b5cad;background:#fbfcfd}
.cat-chip:hover{border-color:#0b5cad;background:#f2f7fd}
.cat-foot{border-top:1px solid #e6e8ec;margin-top:1.6rem;padding-top:1rem;color:#5b6472;font-size:.86rem}
</style>
"""


def label_of(niche: str) -> str:
    """Title-cased label used both as the Blogger label and in the link."""
    return NICHES.get(niche, (str(niche or "general").title(), "", ""))[0]


def label_url(niche: str) -> str:
    """Relative Blogger label-search URL (Apps Script makes it absolute)."""
    return f"/search/label/{quote(label_of(niche), safe='')}"


# ------------------------------------------------------------------ chips ----
def chip_html(niche: str, cfg: dict | None = None) -> str:
    """The small 'More in <Category>' link placed inside every article."""
    if cfg and (cfg.get("categories") or {}).get("show_chips_in_posts") is False:
        return ""
    if not niche:
        return ""
    label, icon, _ = NICHES.get(niche, (str(niche).title(), "\U0001F50D", ""))
    return (
        '<div class="cat-nav">'
        f'<a href="{label_url(niche)}" rel="noopener">'
        f'<span class="cat-nav-icon">{icon}</span> More in {label}'
        "</a>"
        '<a href="/p/categories.html" rel="noopener">All categories &#8594;</a>'
        "</div>"
    )


CHIP_CSS = """
.post-body .cat-nav{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:1.3rem 0;padding:10px 14px;background:#f7f9f8;border:1px solid #e6e8ec;border-radius:10px;font-size:.9rem}
.post-body .cat-nav a{color:#0b5cad;text-decoration:none;font-weight:600}
.post-body .cat-nav a:hover{text-decoration:underline}
.post-body .cat-nav .cat-nav-icon{margin-right:3px}
.post-body .related{margin:1.8rem 0 0;padding-top:1.2rem;border-top:2px solid #e6e8ec}
.post-body .related h3{margin:0 0 .7rem;font-size:1.05rem}
.post-body .related ul{margin:0;padding-left:1.15rem}
.post-body .related li{margin-bottom:.45rem}
"""


def related_placeholder() -> str:
    """Empty div; Apps Script fills it with live same-label posts."""
    return '<div class="related" data-related="1"></div>'


# ------------------------------------------------------------------- counts --
def _queue_entries() -> list[dict]:
    out = []
    if not QUEUE_DIR.exists():
        return out
    for path in sorted(QUEUE_DIR.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _published_entries() -> list[dict]:
    if not STATE_PATH.exists():
        return []
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return state.get("published", []) or []


def counts() -> tuple[Counter, Counter]:
    """(topics planned per niche, articles actually live per niche).

    "Live" counts only what state.json says was published. Queued articles are
    deliberately not counted: during a dry run nothing is committed, so counting
    the queue would inflate the badge and then silently shrink it.
    """
    topics = Counter()
    for row in read_csv_rows(KEYWORDS_CSV):
        n = (row.get("niche") or "").strip()
        if n:
            topics[n] += 1
    made = Counter()
    for entry in _published_entries():
        n = (entry.get("niche") or "").strip()
        if n:
            made[n] += 1
    return topics, made


def list_niches() -> list[str]:
    """Known niches first (in declaration order), then any unknown ones found."""
    topics, _ = counts()
    order = [n for n in NICHES if topics.get(n)]
    extra = sorted(n for n in topics if n not in NICHES)
    return order + extra


# -------------------------------------------------------------------- hub ----
def build_hub(cfg: dict | None = None, state: dict | None = None) -> Path | None:
    """Write content/categories/index.html and return its path."""
    cfg = cfg or {}
    cat_cfg = cfg.get("categories") or {}
    if cat_cfg.get("enabled") is False:
        return None

    topics, made = counts()
    niches = list_niches()
    if not niches:
        return None

    title = cat_cfg.get("page_title", "Browse All Categories")
    total_topics = sum(topics.values())
    total_made = sum(made.values())
    updated = datetime.now(timezone.utc).strftime("%d %B %Y")

    cards = []
    chips = []
    for niche in niches:
        label, icon, desc = NICHES.get(niche, (label_of(niche), "\U0001F50D", ""))
        url = label_url(niche)
        chips.append(f'<a class="cat-chip" href="{url}">{icon} {label}</a>')
        live = made.get(niche, 0)
        badge = (f'<span class="cat-count">{live} published</span>' if live
                 else f'<span class="cat-count">{topics.get(niche, 0)} guides planned</span>')
        desc_html = f'<span class="cat-desc">{desc}</span>' if desc else ""
        cards.append(
            '<li class="cat-card">'
            f'<a href="{url}">'
            f'<span class="cat-name">{icon} {label}</span>'
            f"{desc_html}{badge}</a></li>"
        )

    html = "\n".join([
        HUB_CSS.strip(),
        '<div class="cat-body">',
        f"<h2>{title}</h2>",
        f'<p class="lead">Every guide on this site, sorted by the room or the problem it solves. '
        f"{len(niches)} categories covering {total_topics:,} product types"
        + (f", with {total_made} guides live right now" if total_made else "")
        + ". Each article is written against fixed criteria - capacity, cleaning effort, noise, "
        "material safety, repairability - so you can compare unlike products without guessing.</p>",
        '<ul class="cat-grid">',
        *cards,
        "</ul>",
        "<h2>Jump straight to a category</h2>",
        "<div>",
        *chips,
        "</div>",
        '<div class="cat-foot">',
        f"Last updated {updated}. As an Amazon Associate we earn from qualifying purchases. "
        "Prices and availability change often - the live price on Amazon is the one that counts.",
        "</div>",
        "</div>",
    ])

    hub_rel = cat_cfg.get("hub_path", "content/categories/index.html")
    out = ROOT / hub_rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html + "\n", encoding="utf-8")

    # a tiny manifest so Apps Script knows the page title without parsing HTML
    (out.parent / "index.json").write_text(json.dumps({
        "page_title": title,
        "hub_file": hub_rel,
        "categories": [
            {"niche": n, "label": label_of(n), "url": label_url(n),
             "topics": topics.get(n, 0), "live": made.get(n, 0)}
            for n in niches
        ],
        "total_topics": total_topics,
        "total_live": total_made,
        "updated": updated,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log.info("categories: %d niches, %s planned topics, %s live articles",
             len(niches), f"{total_topics:,}", total_made)
    return out


def markdown_copy(cfg: dict | None = None) -> Path | None:
    """Human-readable archive of the hub (mirrors the queue .md convention)."""
    topics, made = counts()
    niches = list_niches()
    if not niches:
        return None
    cat_cfg = (cfg or {}).get("categories") or {}
    title = cat_cfg.get("page_title", "Browse All Categories")
    lines = [f"# {title}", ""]
    for niche in niches:
        label, icon, desc = NICHES.get(niche, (label_of(niche), "", ""))
        lines.append(f"- **{icon} {label}** - {topics.get(niche, 0)} topics, {made.get(niche, 0)} live")
        if desc:
            lines.append(f"  {desc}")
    out = ROOT / (cat_cfg.get("hub_path", "content/categories/index.html").rsplit(".", 1)[0] + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    from util import load_config, setup_logging

    setup_logging(True)
    _cfg = load_config()
    print(build_hub(_cfg))
    print(markdown_copy(_cfg))
