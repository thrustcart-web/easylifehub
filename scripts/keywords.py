"""Keyword analysis - turns a long-tail keyword into a real product subject.

Without this, "dog crate alternatives that cost less" would become a *product
name* in the title and the affiliate link text, which reads like a machine
wrote it. Instead every keyword is decomposed into:

    base      -> the product itself          ("dog crate")
    audience  -> who it is for               ("apartments")
    budget    -> a price band, if stated     ("under 100 dollars")
    intent    -> review | bestof | comparison | guide | alternatives

`base` is what groups keywords together, so a comparison article always
compares two variants of the SAME product category, and a best-of list always
contains items a shopper would genuinely consider side by side.
"""
from __future__ import annotations

import re

# order matters: longer / more specific patterns first
STRIP_LEADING = [
    # question / informational shapes (added with the expanded keyword pool)
    r"^what\s+nobody\s+tells\s+you\s+about\s+(?:an?\s+)?",
    r"^most\s+overrated\s+",
    r"^what\s+.+?\s+really\s+means\s+in\s+(?:an?\s+)?",
    r"^signs\s+it\s+is\s+time\s+to\s+replace\s+your\s+",
    r"^how\s+often\s+should\s+you\s+replace\s+(?:an?\s+)?",
    r"^how\s+long\s+does\s+(?:an?\s+)?",
    r"^do\s+you\s+really\s+need\s+(?:an?\s+)?",
    r"^how\s+to\s+clean\s+(?:an?\s+)?",
    r"^what\s+to\s+check\s+before\s+buying\s+(?:an?\s+)?",
    r"^common\s+",
    r"^is\s+(?:an?)\s+",
    r"^is\s+",
    r"^are\s+",
    r"^best\s+(?:affordable\s+|budget\s+|cheap\s+)?",
    r"^top\s+\d*\s*(?:best\s+)?",
    r"^affordable\s+",
    r"^budget\s+",
    r"^cheap\s+",
    r"^how\s+to\s+choose\s+(?:an?\s+)?",
    r"^how\s+to\s+(?:pick|find|buy)\s+(?:an?\s+)?",
    r"^common\s+",
    r"^should\s+i\s+buy\s+(?:an?\s+)?",
]

STRIP_TRAILING = [
    # review-angle shapes: keep the product, drop the clause after the colon
    r"\s+review:.*$",
    r":\s+.+rated against real use$",
    r":\s+honest verdict$",
    r":\s+the honest verdict.*$",
    r":\s+.+explained$",
    r"\s+pros and cons.*$",
    r"\s+that actually solve .+$",
    r"\s+that does not feel cheap$",
    r"\s+still worth buying in \d{4}$",
    r"\s+myths that cost people money$",
    r"\s+specs that matter.*$",
    r"\s+for .+ on a budget$",
    # these must run BEFORE the generic "\s+for\s+(.+)$" rule
    r"\s+or\s+.+:\s+which\s+should\s+you\s+buy$",
    r":\s+which\s+one\s+is\s+right\s+for\s+.+$",
    r"^which\s+",
    r"\s+specs\s+that\s+matter\s+and\s+specs\s+that\s+do\s+not$",
    r"\s+myths\s+that\s+cost\s+people\s+money$",
    r"\s+that\s+does\s+not\s+feel\s+cheap$",
    r"\s+that\s+actually\s+lasts?$",
    r"\s+that\s+solve\s+.+$",
    r"\s+mistakes\s+and\s+how\s+to\s+avoid\s+them$",
    r"\s+in\s+\d+\s+minutes$",
    r"\s+on\s+a\s+budget$",
    r"\s+under\s+\d+\s+dollars\s+worth\s+checking$",
    r"\s+properly$",
    r"\s+features$",
    r"\s+should\s+you\s+buy$",
    r"\s+last$",
    r"\s+vs\s+traditional\s+alternatives$",
    r"\s+vs\.?\s+(.+)$",
    r"\s+alternatives\s+that\s+cost\s+less$",
    r"\s+alternatives$",
    r"\s+options$",
    r"\s+picks$",
    r"\s+deals$",
    r"\s+mistakes\s+and\s+how\s+to\s+avoid\s+them$",
    r"\s+what\s+to\s+check\s+before\s+buying$",
    r"\s+buying\s+guide(?:\s+\d{4})?$",
    r"\s+worth\s+it\s+for\s+(.+)$",
    r"\s+for\s+(.+)$",
    r"\s+under\s+(\d+)\s+dollars?$",
    r"\s+reviews?$",
    r"\s+\d{4}$",
]

BUDGET_RE = re.compile(r"\bunder\s+(\d{2,4})\s*(?:dollars?|bucks|\$)?\b", re.I)
# "X vs Y" and the longhand "X or Y: which should you buy" are the same intent
COMPARISON_RE = re.compile(
    r"^(?:\s*(.+?)\s+vs\.?\s+(.+?)\s*|\s*(.+?)\s+or\s+(.+?):\s+which\s+should\s+you\s+buy\s*)$",
    re.I,
)

GUIDE_HINTS = (
    "specs that matter",
    "what nobody tells you",
    "myths that cost people money",
    "how long does",
    "how often should you replace",
    "signs it is time to replace",
    "do you really need",
    "how to clean",
    "most overrated",
    "what to check before buying",
    "buying guide",
    "how to choose",
    "mistakes and how to avoid",
    "alternatives that cost less",
    "worth it",
    "should i buy",
)


def normalise(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s{2,}", " ", text)
    return text


def extract_budget(text: str) -> str:
    match = BUDGET_RE.search(text)
    if not match:
        return ""
    return f"under ${match.group(1)}"


def _comparison_split(text: str) -> tuple[str, str] | None:
    """(left, right) when the keyword is a two-product comparison, else None."""
    match = COMPARISON_RE.match(normalise(text))
    if not match:
        return None
    a, b, c, d = match.groups()
    if a and b:
        return a.strip(), b.strip()
    if c and d:
        return c.strip(), d.strip()
    return None


# A trailing token is only treated as a strippable model number when it has
# enough digits to be one. "Core 300" is part of the product name and must
# survive; "guide 2027" and "v3" are decoration and must not.
_MODEL_TAIL = re.compile(r"\s+\d{1,3}[a-z]?\d*$")


def extract_base(text: str) -> str:
    """The product noun phrase, cleaned of intent decoration."""
    base = normalise(text)
    pair = _comparison_split(base)
    if pair:
        base = pair[0]
    else:
        base = re.sub(r"\s+vs\.?\s+.*$", "", base)
    for pattern in STRIP_TRAILING:
        base = re.sub(pattern, "", base).strip()
    for pattern in STRIP_LEADING:
        base = re.sub(pattern, "", base).strip()
    for pattern in STRIP_TRAILING:
        base = re.sub(pattern, "", base).strip()
    base = re.sub(
        r"\b(?:review|reviews|guide|best|top|cheap|affordable|budget|options|option|picks|deals|vs|which)\b",
        "", base)
    base = re.sub(r"\s{2,}", " ", base).strip(" -:")
    # never let the intent-word cleanup eat a product model number
    base = _MODEL_TAIL.sub("", base).strip(" -:") if re.search(r"\s+\d{4}[a-z]?\d*$", base) else base
    return base or normalise(text)


def extract_audience(text: str) -> str:
    base = normalise(text)
    match = re.search(r"\bfor\s+(.+?)(?:\s+vs\b|\s+under\b|\s*$)", base)
    if match:
        audience = match.group(1).strip()
        audience = re.sub(r"\b(?:beginners?|dummies)\b", "beginners", audience)
        return audience
    match = re.search(r"worth\s+it\s+for\s+(.+)$", base)
    return match.group(1).strip() if match else ""


def detect_intent(text: str) -> str:
    base = normalise(text)
    if COMPARISON_RE.match(base) and " vs traditional alternatives" not in base:
        return "comparison"
    if any(hint in base for hint in GUIDE_HINTS):
        return "guide"
    if base.startswith(("best ", "top ", "affordable ", "budget ", "which ")) or "under" in base:
        return "bestof"
    return "review"


# Sentence debris that must never reach an Amazon search URL.
_JUNK_IN_SEARCH = re.compile(r"^(what|which|how|why|is|are|do|does|should|can|will)\b", re.I)


def _stem(word: str) -> str:
    """Very light plural stripper - enough to spot "documents" == "document"."""
    w = str(word or "").lower().strip()
    if len(w) > 3 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and not w.endswith(("ss", "us", "sh", "ch")):
        return w[:-1]
    if len(w) > 2 and w.endswith("s") and not w.endswith(("ss", "us")):
        return w[:-1]
    return w


def clean_search_term(base: str, audience: str = "") -> str:
    """A clean, Amazon-searchable query built from the product base.

    Public alias: topic_planner calls this for every product entry.
    """
    term = " ".join(str(base or "").split()).strip(" -:,.")
    term = re.sub(r"\s{2,}", " ", term)
    # drop a leftover leading question word ("what even heating means" style)
    if _JUNK_IN_SEARCH.match(term):
        parts = term.split()
        while parts and _JUNK_IN_SEARCH.match(parts[0]):
            parts.pop(0)
        term = " ".join(parts)
    if not term:
        return ""
    aud = " ".join(str(audience or "").split()).strip()
    # Keep the audience only when it narrows the search. "yoga mat for hot yoga"
    # helps; "document safe for documents" is pure repetition, so it goes.
    if aud and len(aud.split()) <= 4 and not _JUNK_IN_SEARCH.match(aud):
        base_stems = {_stem(w) for w in term.lower().split()}
        if not all(_stem(w) in base_stems for w in aud.lower().split()):
            term = f"{term} for {aud}"
    return term


def parse(keyword: str, niche: str = "", row: dict | None = None) -> dict:
    """Full decomposition of one keyword row."""
    row = row or {}
    text = normalise(keyword)
    intent = detect_intent(text)
    base = extract_base(text)

    comparison_with = ""
    pair = _comparison_split(text)
    if intent == "comparison" and pair:
        base = extract_base(pair[0])
        comparison_with = extract_base(pair[1])

    return {
        "keyword": keyword,
        "base": base,
        "niche": niche or row.get("niche", "") or "general",
        "audience": row.get("audience") or extract_audience(text),
        "budget": extract_budget(text),
        "intent": intent,
        "comparison_with": comparison_with,
        "feature": row.get("feature", ""),
        "asin": row.get("asin", ""),
        # what a human would call this product in a headline
        "display_name": title_base(base),
        # The affiliate link must point at the PRODUCT, not at the sentence the
        # keyword was written in. "fabric glue: the honest verdict for fabric
        # work" sent to amazon.com/s?k= returns nothing and earns nothing; the
        # stripped base ("fabric glue") is what Amazon's search can match. The
        # audience stays attached because it narrows results usefully.
        "search_term": clean_search_term(base, row.get("audience") or extract_audience(text)),
    }


ACRONYMS = {
    "sd": "SD", "usb": "USB", "led": "LED", "hd": "HD", "tv": "TV", "diy": "DIY",
    "ac": "AC", "dc": "DC", "wifi": "WiFi", "gps": "GPS", "lcd": "LCD", "oled": "OLED",
    "rgb": "RGB", "obd2": "OBD2", "obdii": "OBD-II", "bluetooth": "Bluetooth",
    "alexa": "Alexa", "iphone": "iPhone", "ipad": "iPad", "macbook": "MacBook",
    "hdmi": "HDMI", "cpu": "CPU", "gpu": "GPU", "ssd": "SSD", "hdd": "HDD",
    "uv": "UV", "hepa": "HEPA", "epa": "EPA", "btu": "BTU", "watt": "Watt",
    "pet": "Pet", "pets": "Pets", "kids": "Kids", "baby": "Baby",
}
SMALL_WORDS = {"a", "an", "the", "and", "or", "for", "of", "in", "on", "with", "to", "vs", "under", "at", "by"}


def title_base(subject: str) -> str:
    """Headline-safe capitalisation of a product noun (acronyms preserved)."""
    words = str(subject).lower().split()
    out = []
    for i, word in enumerate(words):
        if word in ACRONYMS:
            out.append(ACRONYMS[word])
        elif i and word in SMALL_WORDS:
            out.append(word)
        else:
            out.append(word.capitalize())
    return " ".join(out) or str(subject).title()


# ------------------------------------------------------------- families ----
GENERIC_HEADS = {
    "set", "sets", "kit", "kits", "system", "systems", "machine", "machines", "device",
    "devices", "gear", "accessories", "accessory", "stuff", "things", "products", "product",
    "equipment", "tools", "tool", "supplies", "items", "solutions",
}

STOP_TOKENS = {
    "best", "top", "cheap", "affordable", "budget", "options", "option", "picks", "set",
    "kit", "for", "the", "a", "an", "and", "or", "with", "under", "dollars", "review",
    "reviews", "guide", "buying", "vs", "traditional", "alternatives", "alternative",
    "worth", "it", "how", "to", "choose", "common", "mistakes", "avoid", "them", "check",
    "before", "should", "i", "buy", "cost", "less", "that",
}


def head_noun(base: str) -> str:
    """The product family word: 'robot vacuum' -> 'vacuum'."""
    tokens = [t for t in re.split(r"[\s\-/]+", normalise(base)) if t]
    tokens = [t for t in tokens if t not in STOP_TOKENS]
    if not tokens:
        return normalise(base)
    last = tokens[-1]
    # crude singularisation that does not mangle "harness" -> "harne"
    if last.endswith("ies") and len(last) > 4:
        last = last[:-3] + "y"
    elif last.endswith(("ss", "us", "is", "os")) or len(last) <= 4:
        pass
    elif last.endswith("s"):
        last = last[:-1]
    if last in GENERIC_HEADS or len(last) < 4:
        return " ".join(tokens[-2:]) if len(tokens) > 1 else " ".join(tokens)
    return last


def tokens_of(base: str) -> set[str]:
    return {t for t in re.split(r"[\s\-/]+", normalise(base)) if t and t not in STOP_TOKENS}
