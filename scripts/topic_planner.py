"""Topic planner - decides *what* to write next, with no human in the loop.

Rotation rules that keep hundreds of posts from looking machine-made:
  * weighted template rotation    (review / best-of / comparison)
  * niche + family rotation       (never the same subject twice in a row)
  * product-family grouping       (comparisons and roundups only ever contain
                                   products a shopper would really weigh up
                                   together - "stick vacuum vs robot vacuum",
                                   never "dehumidifier vs microfiber cloth")
  * rotating headline patterns    (no two posts share a title formula)
  * base cooldown                 (a subject is not reused for N days)
  * deterministic slugs           (state.json dedupes forever)

Data sources:
  data/products.csv   specific ASINs   -> amazon.com/dp/ASIN?tag=  (highest EPC)
  data/keywords.csv   2,500+ long-tail -> amazon.com/s?k=...&tag=  (zero upkeep)

keywords.csv ships pre-seeded, so the pipeline runs for ~2 years at 3 posts/day
without you ever opening it.
"""
from __future__ import annotations

import random
import re
from collections import defaultdict
from datetime import datetime, timedelta

import keywords as kw
from util import ROOT, load_state, read_csv_rows, slugify, stable_id, today

PRODUCTS_CSV = ROOT / "data" / "products.csv"
KEYWORDS_CSV = ROOT / "data" / "keywords.csv"

TEMPLATES = ("review", "bestof", "comparison")

REVIEW_TITLE_PATTERNS = [
    "{A} {P} Review: What I Would Check Before Buying",
    "{P} Review: What I Would Check Before Buying",
    "{P}: An Honest Look at the Trade-Offs",
    "Is {A} {P} Worth It? A Buyer's Assessment",
    "{P} Buying Guide: The Details Listings Leave Out",
    "{P} Review: Who It Is For and Who Should Skip It",
    "Choosing {A} {P}: Six Things That Actually Matter",
    "{P} Explained: What You Are Really Paying For",
    "Before You Buy {A} {P}: The Honest Version",
]

BESTOF_TITLE_PATTERNS = [
    "Best {F}: {N} Picks Compared Honestly",
    "{N} {F} Options Worth Your Money (and the Ones to Skip)",
    "Best {F}{AUD}: {N} Picks Compared by Real Criteria",
    "{F} Shortlist: {N} Options, Trade-Offs Included",
    "Which {F} Should You Buy? {N} Candidates Compared",
    "{N} {F} Picks Reviewed Against the Criteria That Matter",
]

COMPARISON_TITLE_PATTERNS = [
    "{A} vs {B}: Which One Should You Actually Buy?",
    "{A} or {B}? The Honest Comparison",
    "{A} vs {B}: Deciding Between Two Good Options",
    "Choosing Between {A} and {B}: A Practical Comparison",
]


_PRODUCTS_CACHE: list[dict] | None = None
_FAMILIES_CACHE: dict | None = None


def load_products() -> list[dict]:
    global _PRODUCTS_CACHE
    if _PRODUCTS_CACHE is not None:
        return _PRODUCTS_CACHE
    out = []
    for row in read_csv_rows(PRODUCTS_CSV):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "display_name": name,
                "search_term": name,
                "asin": (row.get("asin") or "").strip().upper(),
                "niche": (row.get("niche") or "general").strip(),
                "feature": row.get("feature", ""),
                "audience": row.get("audience", ""),
                "price": row.get("price", ""),
                "rating": row.get("rating", ""),
                "notes": row.get("notes", ""),
                "_origin": "products.csv",
            }
        )
    _PRODUCTS_CACHE = out
    return out


def load_families() -> dict:
    """{niche: {family: {"bases": [..], "variants": {base: [parsed rows]}}}}

    Grouping happens twice on purpose:
      * family (head noun) -> comparisons, because "stick vacuum vs robot
        vacuum" is a decision a shopper actually faces
      * niche               -> roundups, because "best 5 sleep products" is a
        real listicle whereas most families only contain one or two products
    """
    global _FAMILIES_CACHE
    if _FAMILIES_CACHE is not None:
        return _FAMILIES_CACHE
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in read_csv_rows(KEYWORDS_CSV):
        raw = (row.get("keyword") or "").strip()
        if not raw:
            continue
        parsed = kw.parse(raw, row.get("niche", ""), row)
        base = parsed["base"]
        if len(base.split()) > 5:
            continue
        family = kw.head_noun(base)
        niche = parsed["niche"]
        bucket = out[niche].setdefault(family, {"bases": [], "variants": defaultdict(list)})
        if base not in bucket["bases"]:
            bucket["bases"].append(base)
        bucket["variants"][base].append(parsed)
    _FAMILIES_CACHE = out
    return out


def niche_index(families: dict) -> dict:
    """{niche: {base: [parsed variants]}} - flattened across families."""
    out: dict[str, dict[str, list]] = {}
    for niche, fams in families.items():
        flat: dict[str, list] = {}
        for bucket in fams.values():
            for base, variants in bucket["variants"].items():
                flat.setdefault(base, []).extend(variants)
        out[niche] = flat
    return out


def _shared_tokens(a: str, b: str) -> set:
    return kw.tokens_of(a) & kw.tokens_of(b)


def _find_comparable_pair(bases: list[str], niche_bases: list[str]) -> tuple[str, str] | None:
    """Two products a real shopper would compare, or None."""
    if len(bases) >= 2:
        return bases[0], bases[1]
    if not bases:
        return None
    anchor = bases[0]
    for other in niche_bases:
        if other == anchor:
            continue
        if _shared_tokens(anchor, other):
            return anchor, other
    return None


def pick_template(feasible: list[str], topics: list[dict], weights: dict, rng: random.Random) -> str:
    """Deficit scheduling: whichever shape is furthest below its target share wins.

    This is what stops a 20-post batch from being 19 reviews and 1 comparison.
    """
    if len(feasible) == 1:
        return feasible[0]
    done = len(topics) or 1
    counts = {t: 0 for t in TEMPLATES}
    for topic in topics:
        counts[topic["template"]] = counts.get(topic["template"], 0) + 1
    total_weight = sum(max(1, int(weights.get(t, 30))) for t in TEMPLATES)
    best, best_deficit = feasible[0], -1e9
    for tpl in feasible:
        target = max(1, int(weights.get(tpl, 30))) / total_weight
        deficit = (target * done) - counts[tpl]
        if deficit > best_deficit:
            best, best_deficit = tpl, deficit
    return best


def _weighted_template(weights: dict, recent: list[str], rng: random.Random) -> str:
    pool: list[str] = []
    for tpl, weight in (weights or {}).items():
        if tpl in TEMPLATES:
            pool.extend([tpl] * max(1, int(weight)))
    if not pool:
        pool = list(TEMPLATES)
    fresh = [t for t in pool if not recent or t != recent[-1]]
    return rng.choice(fresh or pool)


def _cooled(subject: str, used: dict, cooldown_days: int) -> bool:
    last = used.get(subject)
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last[:10], "%Y-%m-%d")
    except ValueError:
        return True
    return datetime.utcnow() - last_dt >= timedelta(days=cooldown_days)


def _make_slug(template: str, identity: str, head: str) -> str:
    return f"{slugify(template + '-' + head, 58)}-{stable_id(identity.lower())}"


def _item(variants: list[dict], base: str, niche: str, rng: random.Random) -> dict:
    variant = rng.choice(variants) if variants else {}
    pick = lambda key: next((v[key] for v in variants if v.get(key)), "")  # noqa: E731
    return {
        "name": kw.title_base(base),
        "display_name": kw.title_base(base),
        "search_term": variant.get("keyword") or base,
        "asin": pick("asin"),
        "niche": niche,
        "feature": pick("feature"),
        "audience": variant.get("audience") or pick("audience"),
        "budget": pick("budget"),
        "_origin": "keywords.csv",
    }


def _audience_of(variants: list[dict]) -> str:
    return next((v["audience"] for v in variants if v.get("audience")), "")


# ------------------------------------------------------------------ titles --
PLURAL_ENDINGS = ("s", "ies", "ves", "men", "ies")
MASS_NOUNS = {
    "luggage", "equipment", "bedding", "lighting", "cookware", "cutlery", "software",
    "gear", "furniture", "storage", "lighting", "tableware", "bakeware", "skincare",
}
# heads whose initial letter does not decide the article ("OBD2" is pronounced "oh-bee-dee")
VOWEL_SOUND_HEADS = {"obd2", "obdii", "obd", "ssd", "hdd", "sd", "usb", "hdmi", "lcd", "oled", "rgb", "uv", "hepa", "epa", "x"}


TRAILING_QUALIFIERS = {"set", "sets", "kit", "kits", "system", "systems", "pack", "packs", "combo", "bundle", "unit", "machine"}
BRAND_TOKENS = {  # brand names must never decide the article
    "anker", "levoit", "ninja", "instant", "nutribullet", "kitchenaid", "cuisinart", "breville",
    "delonghi", "philips", "braun", "dyson", "shark", "irobot", "weber", "dewalt", "makita",
    "bosch", "samsung", "lg", "sony", "jbl", "bose", "apple", "logitech", "gopro",
}


def _article(product: str) -> str:
    """'A' / 'An' / '' (plural or uncountable).

    Deliberately does NOT use keywords.head_noun here: that helper strips
    tokens for family grouping, which mangles brand names ('Anker 737 Power
    Bank 24K' -> head 'anker' -> 'Is A Anker...'). Headlines need the real
    trailing noun, with brand tokens ignored.
    """
    text = str(product or "").strip()
    if not text:
        return ""
    if text.lower() in MASS_NOUNS:
        return ""
    raw_tokens = [t for t in re.split(r"[\s\-/]+", text) if t]

    # Proper product names take no article at all:
    #   "Is A Anker 737 Power Bank Worth It?"  -> wrong
    #   "Is Anker 737 Power Bank 24K Worth It?" -> right
    first = raw_tokens[0]
    if first.lower() not in kw.ACRONYMS:      # "OBD2 Scanner" is a common noun phrase
        pass
    looks_branded = first.lower() not in kw.ACRONYMS and (
        first.lower() in BRAND_TOKENS
        or any(ch.isdigit() for ch in first)
        or (first.isupper() and len(first) > 1)
        or (first[:1].isupper() and len(raw_tokens) > 1 and not raw_tokens[1][:1].isupper())
    )
    if looks_branded:
        return ""

    tokens = [t.lower() for t in raw_tokens]
    # drop trailing pack sizes / model numbers: "24k", "300", "nc301", "v3", "2.5"
    while tokens and (tokens[-1] in TRAILING_QUALIFIERS or re.fullmatch(r"[a-z]{0,3}\d+[a-z]?\d*", tokens[-1])):
        tokens.pop()
    # drop leading brand + any leading model numbers
    while tokens and (tokens[0] in BRAND_TOKENS or re.fullmatch(r"\d+[a-z]*", tokens[0])) and len(tokens) > 1:
        tokens.pop(0)
    if not tokens:
        return ""        # "Ninja Creami NC301" -> brand only -> no article
    head = tokens[-1]
    if head in MASS_NOUNS or head in TRAILING_QUALIFIERS:
        return ""
    if head.endswith("s"):
        return ""
    first_token = raw_tokens[0].lower() if raw_tokens else head
    if head in VOWEL_SOUND_HEADS or first_token in VOWEL_SOUND_HEADS:
        return "An"          # "an OBD2 scanner", "an SD card reader" (letter names start with a vowel sound)
    return "An" if head[0] in "aeiou" else "A"


def review_title(product: str, rng: random.Random, used: list | None = None) -> str:
    """Pick a headline pattern the blog has not used recently."""
    pool = [p for p in REVIEW_TITLE_PATTERNS if not used or p not in used[-12:]] or list(REVIEW_TITLE_PATTERNS)
    pattern = rng.choice(pool)
    if used is not None:
        used.append(pattern)
    article = _article(product)
    text = pattern.format(P=product, A=article)
    return re.sub(r"\s{2,}", " ", text.replace("  ", " ")).strip()


NICHE_LABELS = {
    "baby & kids": "Baby and Kids Gear", "budget tech": "Budget Tech", "car": "Car Accessories",
    "coffee & espresso": "Coffee Gear", "fitness": "Home Fitness Gear", "garden": "Garden Tools",
    "home cleaning": "Cleaning Tools", "home office": "Home Office Gear", "kitchen": "Kitchen Appliances",
    "outdoor": "Camping Gear", "personal care": "Personal Care", "pets": "Pet Supplies",
    "sleep": "Sleep Gear", "smart home": "Smart Home Devices", "tools": "Power Tools",
    "travel": "Travel Gear", "audio": "Audio Gear", "electronics": "Everyday Electronics",
    "portable power": "Portable Power", "air quality": "Air Quality", "general": "Everyday Products",
}


def bestof_label(family: str, niche: str) -> str:
    """What the roundup is actually about, phrased the way a shopper would."""
    fam = kw.title_base(family)
    if fam and fam.lower() not in {"general", "options", "products"}:
        plural = fam if fam.endswith(("s", "ch", "sh")) else fam + "s"
        return plural
    return NICHE_LABELS.get(niche.lower(), kw.title_base(niche))


def bestof_title(family: str, niche: str, audience: str, count: int, rng: random.Random, used: list | None = None) -> str:
    label = bestof_label(family, niche)
    pool = [p for p in BESTOF_TITLE_PATTERNS if not used or p not in used[-8:]] or list(BESTOF_TITLE_PATTERNS)
    if not audience:
        pool = [p for p in pool if "{AUD}" not in p] or list(BESTOF_TITLE_PATTERNS)
    pattern = rng.choice(pool)
    if used is not None:
        used.append(pattern)
    aud = f" for {audience.strip().title()}" if audience else ""
    text = pattern.format(F=label, N=count, AUD=aud)
    return re.sub(r"\s{2,}", " ", text).strip()


def comparison_title(a: str, b: str, rng: random.Random, used: list | None = None) -> str:
    pool = [p for p in COMPARISON_TITLE_PATTERNS if not used or p not in used[-6:]] or list(COMPARISON_TITLE_PATTERNS)
    pattern = rng.choice(pool)
    if used is not None:
        used.append(pattern)
    return pattern.format(A=kw.title_base(a), B=kw.title_base(b))


# ------------------------------------------------------------------- planner --
def next_topics(state: dict | None = None, count: int = 3) -> list[dict]:
    from util import load_config

    cfg = load_config()
    content_cfg = cfg.get("content", {})
    weights = content_cfg.get("template_weights", {"review": 55, "bestof": 28, "comparison": 17})
    cooldown = int(content_cfg.get("keyword_cooldown_days", 120))

    state = state or load_state()
    used: dict = state.get("keywords_used", {})
    generated_slugs = {g["slug"] for g in state.get("generated", [])}
    recent_templates = [g.get("template") for g in state.get("generated", [])[-4:]]
    used_titles = state.setdefault("titles_used", [])
    recent_families = [g.get("family") for g in state.get("generated", [])[-3:]]

    products = load_products()
    families = load_families()
    by_niche = niche_index(families)
    fresh_products = [p for p in products if p.get("asin") and _cooled(p["name"], used, cooldown)]

    rng = random.Random(int(today().replace("-", "")) + len(generated_slugs) * 7)
    rng.shuffle(fresh_products)

    # flat list of (niche, family) pairs, shuffled, so niches rotate naturally
    pairs = [(niche, fam) for niche, fams in families.items() for fam in fams]
    rng.shuffle(pairs)

    topics: list[dict] = []
    cursor = 0
    attempts = 0
    # deterministic template ladder so a batch is never all one shape:
    # review -> comparison -> bestof -> then weighted random
    ladder = ["review", "comparison", "bestof"]

    while len(topics) < count and attempts < count * 80:
        attempts += 1
        slot = len(topics)

        # ---- 1) products.csv items get the first slots (finite, best converting)
        product = None
        if fresh_products and slot < len(fresh_products):
            product = fresh_products[slot]
        if product:
            used[product["name"]] = today()          # never review one ASIN twice
            title = review_title(product["name"], rng, used_titles)
            topic = {
                "template": "review",
                "title": title,
                "keyword": product["name"],
                "niche": product["niche"],
                "family": kw.head_noun(product["name"]),
                "items": [product],
                "subject": product["name"],
            }
            topic["slug"] = _make_slug("review", f"{product['name']}|{title}", slugify(product["name"], 40))
            if topic["slug"] in generated_slugs:
                continue
            topics.append(topic)
            recent_templates.append("review")
            recent_families.append(topic["family"])
            continue

        # ---- 2) keyword families, rotating niche and template shape
        if cursor >= len(pairs):
            rng.shuffle(pairs)
            cursor = 0
        niche, family = pairs[cursor]
        cursor += 1
        if recent_families and family in recent_families[-2:]:
            continue

        bucket = families[niche][family]
        bases = [b for b in bucket["bases"] if _cooled(b, used, cooldown)] or bucket["bases"]
        if not bases:
            continue
        rng.shuffle(bases)

        # shapes this subject can actually support
        niche_bases = [b for b in by_niche.get(niche, {}) if _cooled(b, used, cooldown)] or list(by_niche.get(niche, {}))
        feasible = ["review"]
        # A comparison is only worth writing when a shopper would genuinely
        # weigh the two up together. Same product family always qualifies
        # ("stick vacuum vs robot vacuum"); across families we require a shared
        # word ("carpet cleaner" vs "steam mop" -> no, "pet water fountain" vs
        # "pet camera" -> yes). Otherwise the article is noise.
        comparable_pair = _find_comparable_pair(bases, niche_bases)
        if comparable_pair:
            feasible.append("comparison")
        if len(niche_bases) >= 3:
            feasible.append("bestof")
        template = pick_template(feasible, topics, weights, rng)

        if template == "comparison":
            flat = by_niche[niche]
            a_base, b_base = comparable_pair
            title = comparison_title(a_base, b_base, rng, used_titles)
            items = [
                _item(flat.get(a_base) or bucket["variants"].get(a_base, []), a_base, niche, rng),
                _item(flat.get(b_base, []), b_base, niche, rng),
            ]
            topic = {
                "template": "comparison",
                "title": title,
                "keyword": f"{kw.title_base(a_base)} vs {kw.title_base(b_base)}",
                "niche": niche,
                "family": family,
                "items": items,
                "subject": f"{a_base}|{b_base}",
            }
            topic["slug"] = _make_slug(
                "comparison", f"{a_base}|{b_base}|{title}",
                f"{slugify(a_base, 24)}-vs-{slugify(b_base, 24)}")

        elif template == "bestof":
            rng.shuffle(niche_bases)
            picks = niche_bases[: rng.randint(4, 6)]
            flat = by_niche[niche]
            items = [_item(flat[b], b, niche, rng) for b in picks]
            audience = next((i["audience"] for i in items if i.get("audience")), "")
            heads = {kw.head_noun(b) for b in picks}
            label_family = heads.pop() if len(heads) == 1 else ""
            title = bestof_title(label_family, niche, audience, len(items), rng, used_titles)
            topic = {
                "template": "bestof",
                "title": title,
                "keyword": kw.title_base(niche),
                "niche": niche,
                "family": family,
                "items": items,
                "subject": f"{niche}:" + "|".join(sorted(picks)),
            }
            topic["slug"] = _make_slug(
                "bestof", f"{niche}|{family}|{'|'.join(sorted(picks))}|{title}",
                f"{slugify(niche, 16)}-{slugify(family, 22)}")

        else:
            base = bases[0]
            entry = _item(bucket["variants"][base], base, niche, rng)
            title = review_title(entry["display_name"], rng, used_titles)
            topic = {
                "template": "review",
                "title": title,
                "keyword": entry["search_term"],
                "niche": niche,
                "family": family,
                "items": [entry],
                "subject": base,
            }
            topic["slug"] = _make_slug("review", f"{base}|{entry['search_term']}|{title}", slugify(base, 40))

        if topic["slug"] in generated_slugs:
            continue
        topics.append(topic)
        recent_templates.append(topic["template"])
        recent_families.append(family)

    return topics


def mark_used(state: dict, topic: dict) -> None:
    used = state.setdefault("keywords_used", {})
    stamp = today()
    used[topic.get("subject", "")] = stamp
    for item in topic.get("items", []):
        used[item.get("display_name") or item.get("name", "")] = stamp
        used[item.get("search_term", "")] = stamp


def stats() -> dict:
    state = load_state()
    families = load_families()
    subjects = sum(len(b["bases"]) for fams in families.values() for b in fams.values())
    rows = sum(len(v) for fams in families.values() for b in fams.values() for v in b["variants"].values())
    usable_families = sum(1 for fams in families.values() for b in fams.values() if len(b["bases"]) >= 2)
    return {
        "products_csv": len(load_products()),
        "keyword_rows": rows,
        "product_subjects": subjects,
        "comparable_families": usable_families,
        "niches": len(families),
        "generated": state["counters"]["total_generated"],
        "published": state["counters"]["total_published"],
        "queued": len(state.get("queued_slugs", [])),
        "years_of_content_at_3_per_day": round(subjects / (3 * 365), 1) if subjects else 0,
    }


if __name__ == "__main__":
    import json as _json
    import sys

    from util import setup_logging

    setup_logging()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for topic in next_topics(count=n):
        print(f"{topic['template']:11} | {topic['niche']:16} | {topic['title']}")
        for item in topic["items"]:
            print(f"            - {item['display_name']}  ({item['search_term'][:52]})")
