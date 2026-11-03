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
    # unqualified (only used when the row carries no audience/budget/feature)
    "{A} {P} Review: What I Would Check Before Buying",
    "{P} Review: What I Would Check Before Buying",
    "{P}: An Honest Look at the Trade-Offs",
    "Is {A} {P} Worth It? A Buyer's Assessment",
    "{P} Buying Guide: The Details Listings Leave Out",
    "{P} Review: Who It Is For and Who Should Skip It",
    "Choosing {A} {P}: Six Things That Actually Matter",
    "{P} Explained: What You Are Really Paying For",
    "Before You Buy {A} {P}: The Honest Version",
    "{P}: The Questions Worth Asking First",
    # audience-qualified - one distinct headline per real search phrase
    "Best {P} for {AUD}: What Actually Matters",
    "{P} for {AUD}: An Honest Buying Guide",
    "Choosing {A} {P} for {AUD}: Trade-Offs Included",
    "Is {A} {P} Worth It for {AUD}? The Honest Answer",
    "{P} for {AUD}: Five Things Listings Skip",
    "Before You Buy {A} {P} for {AUD}: Read This",
    "{P} for {AUD}: Which Specs Actually Change Anything",
    # budget-qualified
    "Best {P} Under {BUD}: What You Get and What You Lose",
    "{P} Under {BUD}: Honest Expectations",
    "Is {A} {P} Under {BUD} Actually Any Good?",
    "Cheap {P}: What Under {BUD} Really Buys You",
    "{P} Under {BUD}: The Trade-Offs Nobody Mentions",
    # feature-qualified
    "{P} with {FEAT}: Does It Matter or Is It Marketing?",
    "Does {FEAT} Matter in {A} {P}? An Honest Look",
    "{P}: Judging {FEAT} Against Real Use",
    # combined angles
    "Best {P} for {AUD} Under {BUD}: The Real Shortlist",
    "{P} for {AUD}: Judging {FEAT} Honestly",
    "{P} Under {BUD} for {AUD}: What to Check First",
]

BESTOF_TITLE_PATTERNS = [
    "Best {F}: {N} Picks Compared Honestly",
    "{N} {F} Options Worth Your Money (and the Ones to Skip)",
    "Best {F}{AUD}: {N} Picks Compared by Real Criteria",
    "{F} Shortlist: {N} Options, Trade-Offs Included",
    "Which {F} Should You Buy? {N} Candidates Compared",
    "{N} {F} Picks Reviewed Against the Criteria That Matter",
    "Best {F}{AUD}: {N} Options We Would Actually Buy",
    "{N} {F} Compared: Where Each One Wins and Loses",
    "{F} Buying Guide: {N} Picks Ranked by Real Criteria",
    "{N} {F}{AUD}: Honest Comparisons, No Sponsored Picks",
    "The {N} {F} Worth Your Money (and {N2} to Avoid)",
    "{F}: {N} Candidates Judged on the Same Criteria",
    "Best {F}{AUD}: {N} Picks, Ranked by What Matters",
    "{N} {F} Tested Against the Same Checklist",
    "{F} Roundup: {N} Picks With the Trade-Offs Stated",
    "Which {F}{AUD} Is Right for You? {N} Compared",
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
        # The CSV columns are often blank while the angle lives in the keyword
        # text itself ("hanger set for renters on a budget"). Without this the
        # variant carries no audience/budget and the headline cannot use them.
        for key in ("audience", "budget", "intent", "feature"):
            if not (row.get(key) or "").strip() and parsed.get(key):
                row[key] = parsed[key]
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


def _variants_for(variants: list[dict], template: str) -> list[dict]:
    """Only the rows whose search intent matches the article shape.

    Without this the planner happily picks the row "gps watch vs trail map
    case", decides the article is a plain review, and ships a page whose title
    is "GPS Watch Buying Guide" while its target keyword promises a head-to-head.
    The reader (and Google) both notice.
    """
    if not variants:
        return variants
    want_cmp = template == "comparison"
    matching = [v for v in variants if (v.get("intent") == "comparison") == want_cmp]
    return matching or variants


def _item(variants: list[dict], base: str, niche: str, rng: random.Random,
          prefer: dict | None = None) -> dict:
    """Build the product entry from ONE specific keyword row.

    The chosen variant's own audience/budget/feature win. Taking them from any
    sibling row (what `pick` did) mixes angles - a headline would claim
    "Under $50" for an article whose keyword never mentioned a budget, which is
    both wrong and a fabricated-price risk for the quality gate.
    """
    variant = prefer if (prefer in variants if variants else False) else (
        rng.choice(variants) if variants else {})
    pick = lambda key: next((v[key] for v in variants if v.get(key)), "")  # noqa: E731
    audience = variant.get("audience") or pick("audience")
    return {
        "name": kw.title_base(base),
        "display_name": kw.title_base(base),
        # always rebuilt from the product base: the stored keyword column is a
        # human search phrase ("what to check before buying a torque wrench")
        # which Amazon's search cannot match, so it earns nothing.
        "search_term": kw.clean_search_term(base, audience),
        "asin": variant.get("asin") or pick("asin"),
        "niche": niche,
        "feature": variant.get("feature") or pick("feature"),
        "audience": audience,
        "budget": variant.get("budget") or "",
        "_origin": "keywords.csv",
    }


def _audience_of(variants: list[dict]) -> str:
    return next((v["audience"] for v in variants if v.get("audience")), "")


# ------------------------------------------------------------------ titles --
PLURAL_ENDINGS = ("s", "ies", "ves", "men", "ies")
# Noun phrases that end in "s" but are singular, so they still take an article.
# Phrases ending in "s" that are singular, so the plural rule must not strip
# their article: "A French Press", "An Air Mattress", "A Compass".
SINGULAR_S_PHRASES = {
    "air mattress", "mattress", "compass", "dog harness", "french press",
    "heat press", "gas stove", "press", "gas", "glass", "canvas", "lens",
    "series", "brass", "bias", "atlas", "corpus", "status", "bonus",
}
# Adjectives that start a product name but say nothing about brand-vs-common:
# "French Press" is a thing you buy, not a company from France.
NON_BRAND_QUALIFIERS = {
    "french", "german", "italian", "japanese", "portable", "electric",
    "manual", "digital", "digital", "wireless", "cordless", "adjustable",
    "folding", "inflatable", "insulated", "orthopedic", "ergonomic",
}

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


# Ordinary English words that appear in product names. A capitalised first
# token that is in here is NOT a brand, so the phrase still takes an article:
#   "French Press" -> "A French Press", while "Anker Power Bank" -> no article.
ENGLISH_WORDS = {
    "ab", "about", "absorbency", "absorber", "ac", "acceptance", "accessories", "accessory", "accuracy", "acne",
    "across", "acrylic", "action", "actually", "adapter", "adhesive", "adjustability", "adjustable", "aerator",
    "affordable", "age", "aid", "air", "airflow", "airline", "airlines", "airtight", "alarm", "alexa", "allergy",
    "alternatives", "amps", "an", "and", "anti", "anxious", "apartment", "apartments", "app", "appliances", "archive",
    "are", "area", "areas", "arm", "arrivals", "arthritis", "as", "assembly", "at", "attachment", "audio", "auto",
    "autofocus", "automatic", "availability", "avoid", "awareness", "baby", "back", "backdrop", "backing", "backpack",
    "backpackers", "backpacking", "bag", "bags", "bakers", "bakeware", "baking", "balance", "balconies", "ball",
    "bamboo", "bands", "bank", "bar", "basements", "basket", "baskets", "bass", "batch", "bath", "bathrooms", "battery",
    "bay", "be", "beach", "beading", "bean", "beanie", "beard", "bed", "bedding", "bedrooms", "beds", "bedside",
    "been", "before", "beginner", "beginners", "being", "belt", "belts", "bench", "bento", "best", "bike", "bin",
    "binder", "binding", "bins", "bird", "blackout", "bladder", "blade", "blanket", "bleed", "blender", "blocking",
    "blocks", "blood", "blower", "blue", "blues", "bluetooth", "board", "body", "boil", "bolt", "bond", "bone",
    "bookshelf", "bored", "boss", "bottle", "bowl", "box", "brace", "braces", "braided", "bread", "breaks", "breathability",
    "breeds", "brew", "brewers", "brewing", "bridge", "brightness", "bristle", "brush", "btu", "bucket", "buckle",
    "budget", "budgets", "build", "building", "bulb", "bulk", "bullet", "burn", "burr", "business", "businesses",
    "busy", "but", "button", "buy", "buyers", "buying", "by", "cabinet", "cable", "cages", "cake", "calendar",
    "calibration", "caliper", "calls", "cam", "camera", "cameras", "camp", "campers", "campervans", "camping",
    "can", "cancelling", "cane", "canister", "canvas", "canvases", "capacity", "capture", "car", "carbon", "carbonation",
    "card", "cardio", "care", "carpet", "carrier", "carry", "cartridge", "case", "cashmere", "cast", "cat", "cats",
    "ceiling", "ceilings", "ceramic", "certification", "chair", "change", "charger", "charging", "chat", "cheap",
    "check", "checked", "checking", "chef", "chew", "chewers", "choose", "chopper", "chuck", "circular", "cities",
    "city", "clamp", "clarity", "class", "clean", "cleaner", "cleaning", "cleans", "cleansing", "climate", "climates",
    "clipper", "clock", "closet", "closets", "cloth", "clothes", "clothesline", "clothing", "cluttered", "co",
    "coat", "coated", "coating", "code", "coffee", "colander", "cold", "colic", "collectors", "colour", "combo",
    "comfort", "common", "commuters", "commutes", "compact", "compartment", "compass", "compatibility", "compost",
    "compressibility", "compression", "computing", "concentrate", "conditions", "conduction", "connectivity", "connector",
    "consistent", "console", "container", "containers", "content", "control", "controller", "controls", "cooker",
    "cooking", "cooks", "cooktops", "cookware", "cool", "cooling", "cordless", "cork", "corners", "corrector",
    "cost", "cotton", "count", "country", "couples", "cover", "coverage", "covered", "covers", "craft", "crafts",
    "crate", "crawlers", "crease", "crochet", "cross", "crossbody", "crowded", "cubes", "cuff", "cup", "curling",
    "curtain", "curtains", "curved", "cushion", "cushions", "cut", "cuts", "cutter", "cutters", "cutting", "daily",
    "dairy", "damp", "dark", "darkness", "dash", "dashboard", "data", "dates", "day", "daycare", "daypack", "dc",
    "deadbolt", "deck", "decor", "decorative", "deep", "deer", "dehumidifier", "dehydrator", "delicates", "delivery",
    "deployment", "depth", "derma", "design", "desk", "desking", "desks", "desktop", "detail", "detection", "detector",
    "detergent", "device", "diagnostics", "diameter", "diaper", "digital", "digitising", "dimming", "dishwasher",
    "dispenser", "display", "distribution", "diy", "do", "dock", "docking", "document", "documents", "does", "dog",
    "dogs", "dollars", "door", "doorbell", "doors", "doorway", "dotted", "double", "down", "drain", "drainage",
    "dried", "drill", "drilling", "drinkers", "drinks", "drip", "drive", "driven", "driver", "drivers", "drives",
    "driveways", "driving", "dry", "dryer", "drying", "dual", "duffel", "dumbbell", "dumbbells", "durability",
    "dust", "duster", "dusting", "dusty", "dutch", "duvet", "dwellers", "ear", "earbuds", "early", "ease", "easy",
    "eaters", "ecosystems", "edge", "editing", "editors", "efficiency", "electric", "electronics", "electrostatic",
    "emergency", "energy", "entry", "entryway", "entryways", "epilator", "ergonomic", "escape", "espresso", "even",
    "evening", "exam", "exercise", "expander", "expiry", "extension", "external", "extinguisher", "extraction",
    "eye", "eyes", "eyewear", "fabric", "fabrics", "face", "facial", "false", "families", "family", "fan", "fans",
    "fashion", "fast", "fastening", "features", "feeder", "feeding", "feeds", "feel", "fence", "festivals", "fibre",
    "field", "file", "filler", "film", "filter", "finder", "fine", "fineness", "finish", "finishing", "fire", "fireproof",
    "firmness", "first", "fit", "fitment", "fitness", "fixed", "flash", "flat", "fleet", "flicker", "flights",
    "floor", "floors", "flosser", "flow", "flowers", "fluctuation", "flutter", "flyers", "foam", "focused", "foldability",
    "foldable", "folder", "folding", "food", "footprint", "footrest", "for", "forgetful", "fork", "fountain", "four",
    "fps", "frame", "frames", "free", "freezer", "french", "frequency", "frequent", "freshener", "fridge", "fridges",
    "friendly", "from", "frother", "fryer", "fuel", "furniture", "fussy", "gallery", "gaming", "gan", "gap", "garage",
    "garages", "garden", "gardeners", "gardening", "gardens", "garlic", "garment", "gas", "gate", "gear", "gel",
    "gentleness", "ghosting", "gift", "gifts", "gimbal", "glare", "glass", "glasses", "glaze", "gloves", "glue",
    "goers", "goods", "google", "gps", "grade", "grass", "grater", "grid", "grill", "grind", "grinder", "grinding",
    "grip", "grit", "grooming", "grout", "grow", "growing", "guard", "guide", "gums", "gun", "gym", "gyms", "hair",
    "hallways", "hammer", "hammock", "hamper", "hand", "handed", "handheld", "handle", "handles", "hands", "hanger",
    "hanging", "hard", "hardware", "hardwood", "harness", "haul", "hdmi", "head", "headlamp", "headphones", "headset",
    "healing", "health", "heart", "heat", "heating", "heavy", "height", "hepa", "high", "highlighter", "hikers",
    "hikes", "hiking", "hobbies", "hobbyists", "hold", "holder", "holiday", "home", "homeowners", "homes", "honey",
    "hooded", "hook", "hooks", "hose", "hostel", "hot", "house", "household", "households", "houses", "how", "hub",
    "hubs", "humid", "humidifier", "hunters", "hvac", "hydration", "iced", "if", "immersion", "impact", "in", "indoor",
    "induction", "inflator", "infuser", "ink", "input", "inspection", "installation", "instant", "insulated", "intensity",
    "interactive", "interface", "interior", "interviews", "ionic", "iron", "ironing", "irrigation", "is", "isolation",
    "it", "item", "jacket", "jam", "jars", "jewellery", "jigsaw", "jobs", "joint", "journalers", "journaling",
    "jump", "kayakers", "kettle", "kettlebell", "key", "keyboard", "keypad", "keys", "kids", "kink", "kit", "kitchen",
    "kitchens", "knee", "kneeling", "knife", "knitting", "label", "labelling", "ladder", "lag", "lamp", "landscape",
    "lantern", "lap", "laptop", "large", "laser", "last", "lasts", "late", "latency", "latte", "laundry", "lavalier",
    "lawn", "lawns", "lay", "layering", "layout", "lcd", "leaf", "leak", "learning", "leash", "leather", "led",
    "left", "legal", "length", "lens", "less", "level", "lid", "life", "lift", "lifters", "lifting", "light", "lighting",
    "lightning", "lights", "limit", "limited", "limits", "line", "lining", "lint", "lit", "litter", "living", "load",
    "local", "locations", "lock", "locks", "loft", "long", "longevity", "loofah", "loop", "loose", "losing", "loudness",
    "low", "luggage", "lumbar", "lunch", "lunches", "lux", "mac", "machine", "machines", "magsafe", "maintenance",
    "maker", "makers", "makeup", "making", "management", "mandoline", "manicure", "map", "mask", "mason", "mass",
    "massage", "massager", "mat", "matching", "material", "matter", "mattress", "mature", "meal", "measure", "measuring",
    "meat", "mechanical", "media", "meeting", "memory", "mesh", "mic", "microfiber", "microphone", "mid", "milk",
    "mini", "minimalist", "minimalists", "minutes", "mirror", "mistakes", "mitt", "mixability", "mixed", "mixer",
    "mobile", "mobility", "moka", "money", "monitor", "monitoring", "monoxide", "mood", "mop", "mornings", "mortar",
    "most", "motion", "motor", "mount", "mounting", "mounts", "mouse", "movement", "moving", "mower", "mp", "muffin",
    "mulch", "multi", "multimeter", "myths", "nail", "navigation", "nebuliser", "neck", "need", "needed", "needle",
    "needles", "neighbourhoods", "neighbours", "nervous", "netting", "new", "night", "no", "nobody", "noise", "noisy",
    "nomads", "non", "nonstick", "nose", "not", "note", "notebook", "nurseries", "obd", "occasional", "odour",
    "of", "off", "office", "offices", "often", "oil", "older", "oled", "olive", "on", "one", "only", "open", "opener",
    "operation", "options", "or", "oral", "organisation", "organiser", "organizer", "orthopedic", "oscillating",
    "out", "outdoor", "outlet", "output", "oven", "over", "overrated", "overspray", "owners", "oximeter", "pack",
    "packability", "package", "packed", "packers", "packing", "pad", "padding", "pain", "paint", "painting", "pairing",
    "pan", "panel", "pantry", "paper", "parents", "parts", "pass", "passport", "passthrough", "patches", "path",
    "patio", "patios", "peak", "peephole", "pegboard", "pen", "pencil", "people", "performance", "period", "person",
    "personal", "pestle", "pet", "pets", "phone", "phono", "photo", "photographers", "photography", "picky", "picnics",
    "picture", "pigment", "pile", "pill", "pilling", "pillow", "pitcher", "placement", "plan", "planner", "plant",
    "planter", "plants", "plate", "platform", "play", "player", "players", "plug", "plugs", "pocket", "podcasters",
    "polarised", "poles", "poop", "poor", "pop", "port", "portability", "portable", "portion", "positional", "posture",
    "pot", "pots", "pour", "powder", "power", "preamp", "precise", "precision", "prep", "preppers", "preservation",
    "press", "pressure", "priming", "printer", "privacy", "processor", "product", "programmable", "project", "projector",
    "projects", "prone", "proofing", "properly", "protection", "protector", "protein", "protocol", "pruning", "pull",
    "pullers", "pulse", "puncture", "puppies", "purifier", "quality", "quick", "quiet", "quilting", "racing", "rack",
    "rain", "raised", "range", "ratchet", "rate", "rating", "ratio", "raw", "reach", "readability", "reader", "readers",
    "reading", "really", "rear", "recline", "record", "recorder", "recording", "recovery", "red", "reducers", "reel",
    "refill", "reflective", "reflectors", "refresh", "refreshes", "refurbished", "regulation", "rehab", "relief",
    "remote", "remover", "renters", "repairs", "replace", "requirement", "residue", "resistance", "response", "rest",
    "restores", "retention", "reusable", "rfid", "rgb", "rice", "rides", "rideshare", "right", "rigidity", "ring",
    "rinse", "riser", "risk", "road", "robot", "rod", "rods", "roll", "roller", "room", "rooms", "rope", "rotary",
    "rowing", "rug", "rugs", "run", "runner", "runners", "runtime", "rust", "sack", "safe", "safety", "sandpaper",
    "santoku", "savers", "saw", "scale", "scalp", "scanner", "scarf", "scene", "scheduling", "school", "scissors",
    "scoop", "scrapbooking", "scraper", "scratch", "scratcher", "screen", "screens", "screwdriver", "sd", "seal",
    "sealer", "sealing", "season", "seasonal", "seasoning", "seat", "seating", "second", "security", "seed", "selection",
    "self", "senior", "seniors", "sensitive", "sensor", "series", "sessions", "set", "setup", "setups", "sewing",
    "shade", "shaker", "shampoo", "shaped", "shared", "sharpener", "sharpness", "shaver", "shavers", "shaving",
    "shears", "shedders", "shedding", "sheds", "sheet", "sheets", "shelf", "shelves", "shift", "shifts", "shirt",
    "shock", "shoe", "shooters", "shoots", "shoppers", "short", "shotgun", "shots", "should", "shower", "shredder",
    "shut", "side", "sided", "sifting", "signs", "simple", "size", "sizing", "sketchbook", "sketching", "skillet",
    "skin", "sleep", "sleepers", "sleeping", "sleeve", "slicer", "slip", "slopes", "slow", "small", "smart", "smartphone",
    "smartwatch", "smear", "smoke", "smoothie", "smoothness", "snack", "snacks", "snap", "socket", "socks", "soda",
    "softbox", "softness", "soil", "solar", "soldering", "solo", "solve", "sorting", "sound", "soundbar", "soundstage",
    "source", "sourcing", "space", "spaces", "sparkling", "spatula", "speaker", "speakers", "specs", "spectrum",
    "speed", "spice", "spiral", "spoon", "spray", "sprayer", "spread", "squeegee", "ssd", "stability", "stackability",
    "stacking", "stages", "stain", "stainless", "stairs", "stamping", "stand", "standing", "stands", "stapler",
    "staples", "starter", "starting", "static", "station", "stays", "steam", "steamer", "steel", "steep", "steering",
    "steriliser", "sterilizer", "stick", "stitch", "stitching", "stool", "storage", "storm", "stove", "straightener",
    "strap", "straps", "streak", "stream", "streamers", "streaming", "street", "strength", "string", "strip", "stroller",
    "stud", "students", "studio", "studios", "stuffy", "styling", "suction", "sufferers", "suitability", "suitcase",
    "sun", "sunglasses", "sunny", "sunrise", "supplements", "supplies", "support", "surface", "surfaces", "surge",
    "suv", "swimmers", "switch", "system", "tab", "table", "tablet", "takers", "tall", "tamper", "tank", "tape",
    "tarnish", "tea", "team", "tech", "teeth", "tells", "temperature", "tension", "tent", "tester", "textiles",
    "texture", "that", "the", "theatre", "theft", "them", "therapy", "thermometer", "thermostat", "thick", "thickness",
    "thread", "through", "throw", "tier", "tight", "tile", "time", "timer", "tin", "tiny", "tip", "tire", "to",
    "toaster", "toddler", "toddlers", "toiletry", "tongs", "tongue", "tool", "tools", "toothbrush", "topper", "torch",
    "torque", "tote", "touchscreen", "towel", "tower", "toy", "toys", "tracker", "trackers", "tracking", "trackpad",
    "trades", "traditional", "traffic", "trail", "trainees", "training", "transfer", "transmitter", "transparency",
    "travel", "travellers", "tray", "treadmill", "tree", "trekking", "trimmer", "tripod", "trips", "trowel", "trunk",
    "tsa", "tub", "tube", "tummy", "turnarounds", "turntable", "tv", "tweezers", "twins", "type", "types", "typists",
    "ultralight", "ultrasonic", "ultrawide", "umbrella", "under", "up", "update", "upholstery", "upper", "usb",
    "use", "users", "utensil", "utensils", "utility", "uv", "vacuum", "value", "valve", "variety", "vase", "vegetable",
    "vent", "ventilation", "venting", "vessels", "vest", "vet", "video", "view", "vinyl", "vision", "vitamin",
    "vloggers", "voice", "vs", "waffle", "walk", "walkers", "walking", "walks", "wall", "wallet", "walls", "wand",
    "wardrobes", "warmth", "warp", "warranty", "was", "wash", "washability", "washers", "washing", "waste", "watch",
    "watchers", "water", "waterers", "watering", "waterproofing", "wax", "wear", "wearables", "wearers", "wearing",
    "weather", "weatherproofing", "webcam", "wedding", "weekend", "weeknights", "weeks", "weight", "weighted",
    "wellness", "were", "wet", "what", "wheel", "wheelbarrow", "when", "which", "white", "whiteboard", "width",
    "wifi", "wind", "window", "windows", "windshield", "winter", "wipes", "wireless", "wiring", "with", "wooden",
    "woodworking", "wool", "work", "workdays", "workers", "workplaces", "workshops", "workwear", "worth", "wow",
    "wrench", "wrist", "write", "writers", "writing", "xl", "yards", "yarn", "yoga", "you", "your", "zipper", "zone",
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
    # skip a leading model-number token: "3D Printer Pen" is a common noun
    # phrase even though "3D" is not a word
    lead = 0
    while lead < len(raw_tokens) - 1 and re.fullmatch(r"\d+[a-z]*|[a-z]{0,3}\d+[a-z]?\d*",
                                                     raw_tokens[lead].lower()):
        lead += 1
    first = raw_tokens[lead]
    # "OBD2 Scanner" and "LED Strip Lights" are common noun phrases, not brands.
    # The old rule also treated any second-capitalised-word phrase as a brand,
    # which silently dropped the article from "French Press" and "Gas Stove".
    # Now: a brand is a known brand token, contains a digit, is an ALL-CAPS
    # acronym outside our letter-sound list, or is a capitalised non-word.
    looks_branded = first.lower() not in kw.ACRONYMS and (
        first.lower() in BRAND_TOKENS
        or any(ch.isdigit() for ch in first)
        or (first.isupper() and len(first) > 1 and first.lower() not in VOWEL_SOUND_HEADS)
        or (first[:1].isupper() and first.lower() not in ENGLISH_WORDS
            and first.lower() not in NON_BRAND_QUALIFIERS)
    )
    if looks_branded:
        return ""

    # whole-phrase checks first: "Cookware" and "Cast Iron Skillet Set" are
    # uncountable/plural even though the single-word tests below miss them
    phrase = " ".join(x.lower() for x in raw_tokens)
    if phrase in MASS_NOUNS:
        return ""
    if phrase.endswith(("equipment", "furniture", "luggage", "lighting", "cookware",
                        "cutlery", "bedding", "software", "gear", "storage")):
        return ""

    # A trailing collection word makes the whole phrase plural: you buy
    # "Resistance Bands" (no article), so "Resistance Bands Set" gets none either.
    if len(raw_tokens) > 1 and raw_tokens[-1].lower() in TRAILING_QUALIFIERS \
            and raw_tokens[-1].lower() in {"set", "sets", "kit", "kits", "pack", "packs",
                                           "bundle", "combo", "system", "systems"}:
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
    # Plural nouns take no article - but only when the whole phrase is plural.
    # "Air Fryer" ends in a consonant; "Cotton Sheets" does not. Judging on the
    # head alone would happily produce "Is A Adjustable Dumbbells Worth It?".
    if (head.endswith("s") and phrase.endswith("s")
            and phrase not in SINGULAR_S_PHRASES
            and head not in SINGULAR_S_PHRASES):
        return ""

    # The article follows the FIRST sound of the phrase, not the head noun:
    # "an Air Fryer" (not "a"), but still "an SD Card" because the letter name
    # "S" is pronounced /es/. Acronym exceptions are matched on either token.
    first_token = raw_tokens[0].lower() if raw_tokens else head
    if first_token in VOWEL_SOUND_HEADS or head in VOWEL_SOUND_HEADS:
        return "An"
    sound = first_token[0] if first_token else head[0]
    return "An" if sound in "aeiou" else "A"


# Title-case words that read better lowercase inside a qualifier phrase.
_SMALL_IN_QUALIFIER = {
    "a", "an", "the", "and", "or", "for", "of", "in", "on", "with", "to", "at",
    "by", "under", "over", "your", "their", "its", "his", "her",
}


def _title_phrase(text: str) -> str:
    """Human title-casing: keeps small words small, keeps acronyms intact."""
    out = []
    for i, word in enumerate(str(text or "").strip().split()):
        low = word.lower()
        if i and low in _SMALL_IN_QUALIFIER:
            out.append(low)
        elif low in kw.ACRONYMS:
            out.append(kw.ACRONYMS[low])
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def qualifiers(entry: dict | None) -> dict:
    """Angle words pulled from the *specific* keyword row being written.

    Two rows that share a product base ("best air fryer for small kitchens" and
    "air fryer under 50 dollars") are different searches with different intent,
    so the headline has to reflect the angle - otherwise a 6,000-article blog
    ends up with 2,000 copies of "Air Fryer Review: What I Would Check Before
    Buying", which Google treats as duplicate content.
    """
    entry = entry or {}
    aud = _title_phrase(entry.get("audience") or "")
    budget = str(entry.get("budget") or "").strip()
    budget = budget.replace("under $", "").replace("$", "").strip()
    budget = f"${budget}" if budget.isdigit() else ""
    feat = _title_phrase(entry.get("feature") or "")
    return {"AUD": aud, "BUD": budget, "FEAT": feat}


def _clean_title(text: str) -> str:
    text = re.sub(r"\s{2,}", " ", text.replace("  ", " "))
    # strip dangling separators left behind when a qualifier was empty
    text = re.sub(r"\s+([,:])\s*([,:])", r"\1", text)
    text = re.sub(r"\s+for\s*([,:])", r"\1", text)
    text = re.sub(r"\s+under\s*([,:])", r"\1", text)
    text = re.sub(r"\s+with\s*([,:])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -:,")


def review_title(product: str, rng: random.Random, used: list | None = None,
                 entry: dict | None = None, seen_titles: set | None = None) -> str:
    """Pick a headline pattern the blog has not used on this subject before.

    `used` keeps the recent-pattern rotation varied; `seen_titles` is the hard
    guarantee - a title that already exists anywhere on the blog is retried,
    because duplicate headlines are duplicate pages as far as Google cares.
    """
    q = qualifiers(entry)
    article = _article(product)
    pool = list(REVIEW_TITLE_PATTERNS)
    if not q["AUD"]:
        pool = [p for p in pool if "{AUD}" not in p] or pool
    if not q["BUD"]:
        pool = [p for p in pool if "{BUD}" not in p] or pool
    if not q["FEAT"]:
        pool = [p for p in pool if "{FEAT}" not in p] or pool

    fresh = [p for p in pool if not used or p not in used[-10:]] or pool
    ordered = list(fresh)
    rng.shuffle(ordered)

    fallback = ""
    for pattern in ordered:
        text = _clean_title(pattern.format(P=product, A=article, **q))
        if not text:
            continue
        if not fallback:
            fallback = text
        if seen_titles is None or text.lower() not in seen_titles:
            if used is not None:
                used.append(pattern)
            return text

    # every pattern for this angle already exists on the blog: keep the headline
    # honest by naming the angle explicitly rather than inventing a variation
    if used is not None and ordered:
        used.append(ordered[0])
    suffix = " | ".join(x for x in (q["AUD"], q["BUD"], q["FEAT"]) if x)
    text = f"{fallback} ({suffix})" if suffix else fallback
    if seen_titles is not None and text.lower() in seen_titles:
        n = 2
        while f"{text} {n}".lower() in seen_titles and n < 60:
            n += 1
        text = f"{text} {n}"
    return text


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


def bestof_title(family: str, niche: str, audience: str, count: int, rng: random.Random,
                 used: list | None = None, seen_titles: set | None = None) -> str:
    label = bestof_label(family, niche)
    pool = list(BESTOF_TITLE_PATTERNS)
    if not audience:
        pool = [p for p in pool if "{AUD}" not in p] or pool
    fresh = [p for p in pool if not used or p not in used[-8:]] or pool
    ordered = list(fresh)
    rng.shuffle(ordered)

    aud = f" for {_title_phrase(audience)}" if audience else ""
    fallback = ""
    for pattern in ordered:
        text = _clean_title(pattern.format(F=label, N=count, N2=max(2, count - 2), AUD=aud))
        if not fallback:
            fallback = text
        if seen_titles is None or text.lower() not in seen_titles:
            if used is not None:
                used.append(pattern)
            return text
    if used is not None and ordered:
        used.append(ordered[0])
    text = f"{fallback} ({_title_phrase(audience)})" if audience else fallback
    if seen_titles is not None and text.lower() in seen_titles:
        n = 2
        while f"{text} {n}".lower() in seen_titles and n < 60:
            n += 1
        text = f"{text} {n}"
    return text


def comparison_title(a: str, b: str, rng: random.Random, used: list | None = None,
                     seen_titles: set | None = None) -> str:
    left, right = kw.title_base(a), kw.title_base(b)
    pool = [p for p in COMPARISON_TITLE_PATTERNS if not used or p not in used[-6:]] or list(COMPARISON_TITLE_PATTERNS)
    ordered = list(pool)
    rng.shuffle(ordered)
    fallback = ""
    for pattern in ordered:
        text = _clean_title(pattern.format(A=left, B=right))
        if not fallback:
            fallback = text
        if seen_titles is None or text.lower() not in seen_titles:
            if used is not None:
                used.append(pattern)
            return text
    if used is not None and ordered:
        used.append(ordered[0])
    return fallback


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
    # every headline the blog has ever produced - the hard anti-duplicate guard
    seen_titles = {str(g.get("title", "")).strip().lower()
                   for g in state.get("generated", []) if g.get("title")}
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
            title = review_title(product["name"], rng, used_titles,
                                   entry=product, seen_titles=seen_titles)
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
            title = comparison_title(a_base, b_base, rng, used_titles, seen_titles)
            a_pool = _variants_for(flat.get(a_base) or bucket["variants"].get(a_base, []), "comparison")
            b_pool = _variants_for(flat.get(b_base, []), "comparison")
            items = [
                _item(a_pool, a_base, niche, rng),
                _item(b_pool, b_base, niche, rng),
            ]
            # The affiliate link should point at the product, not at the whole
            # "X vs Y" sentence - Amazon's search copes with neither.
            for item, base_name in ((items[0], a_base), (items[1], b_base)):
                item["search_term"] = kw.title_base(base_name)
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
            items = [_item(_variants_for(flat[b], "bestof"), b, niche, rng) for b in picks]
            audience = next((i["audience"] for i in items if i.get("audience")), "")
            heads = {kw.head_noun(b) for b in picks}
            label_family = heads.pop() if len(heads) == 1 else ""
            title = bestof_title(label_family, niche, audience, len(items), rng,
                             used_titles, seen_titles)
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
            pool = _variants_for(bucket["variants"][base], "review")
            chosen = rng.choice(pool) if pool else {}
            entry = _item(pool, base, niche, rng, prefer=chosen)
            title = review_title(entry["display_name"], rng, used_titles,
                                 entry=entry, seen_titles=seen_titles)
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
        seen_titles.add(topic["title"].strip().lower())
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
