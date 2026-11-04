"""Offline article engine - the safety net that makes the system unkillable.

If every free AI provider is out of quota, this module still produces a
complete, well-structured, SEO-valid article from the topic data alone. The
copy is genuinely useful (buyer-criteria driven, not lorem ipsum) and every
post still carries the affiliate CTAs, FAQ block and schema.org markup.

It is also used to enrich AI output: comparison tables, buying-guide sections
and FAQ schema are merged in regardless of which writer produced the body.
"""
from __future__ import annotations

from util import money

CRITERIA = {
    "general": [
        ("Build quality and materials", "Cheap housings crack and cheap hinges fail first. Look for metal internals, reinforced joints and a finish that survives daily handling."),
        ("Real-world performance", "Spec sheets flatter everything. What matters is how the product behaves in the situation you actually bought it for - not in a lab."),
        ("Ease of use and setup", "If the first ten minutes are frustrating, the honeymoon ends fast. Good manuals, sensible defaults and app-free operation are worth paying for."),
        ("Running costs", "Filters, blades, pods, subscriptions and replacement parts. A low sticker price with expensive consumables is not a bargain."),
        ("Warranty and support", "One-year coverage is the floor. Brands that stand behind a product publish clear warranty terms and answer support emails."),
        ("Verified owner feedback", "Read the three-star reviews. Five-star reviews tell you about marketing; three-star reviews tell you about the product."),
    ],
    "electronics": [
        ("Battery life under real load", "Manufacturer numbers are measured at minimum brightness with wifi off. Halve them for a realistic daily figure."),
        ("Ports and connectivity", "Count what you actually plug in. Dongle life is a tax on every desk."),
        ("Software and update policy", "A companion app that stops being updated turns a smart device into a dumb one within two years."),
        ("Repairability", "Glued-in batteries and proprietary screws decide whether a two-year-old device is fixable or landfill."),
        ("Noise and heat", "Fans and thermal throttling are the difference between a tool you enjoy using and one you tolerate."),
        ("Warranty and support", "Check whether the brand honours warranty in your country, not just where it is manufactured."),
    ],
    "kitchen": [
        ("Capacity vs. counter space", "Buy for the way you cook on a normal Tuesday, not for the dinner party you host twice a year."),
        ("Cleaning effort", "If it has crevices, it will collect residue. Dishwasher-safe parts genuinely change how often you use it."),
        ("Material safety and durability", "Food-grade stainless steel, PFOA-free coatings and no warping at high heat."),
        ("Noise level", "A blender or grinder at 7am is a household argument waiting to happen."),
        ("Spare parts availability", "Blades, gaskets and carafes should be buyable separately two years from now."),
        ("Warranty and support", "Motor warranties of three years or more signal a manufacturer that trusts its own build."),
    ],
    "home": [
        ("Fit for your space", "Measure twice. Product photos use wide-angle lenses and small rooms to flatter scale."),
        ("Assembly time and tools", "Count the fasteners and check whether a tool is included. Two hours is the patience ceiling for most people."),
        ("Materials and load rating", "Particle board and real wood behave very differently after a humid summer."),
        ("Maintenance", "Washable covers, replaceable filters and surfaces that do not show every fingerprint."),
        ("Noise in operation", "Anything with a motor or a compressor will be heard at night."),
        ("Return policy", "Bulky items are expensive to send back. Check the window before you buy."),
    ],
    "fitness": [
        ("Stability under load", "Wobble is the first thing that kills motivation - and the first thing cheap frames do."),
        ("Adjustability", "If it does not fit your height and limb length, your form suffers and so do your joints."),
        ("Tracking accuracy", "Optical heart rate and step counting drift. Treat the numbers as trends, not measurements."),
        ("Comfort over duration", "Padding, grip and breathability decide whether you last 20 minutes or 60."),
        ("App lock-in", "Check that your data can be exported and that core features are not behind a subscription."),
        ("Durability and warranty", "Sweat is corrosive. Look for sealed bearings and a frame warranty of at least two years."),
    ],
}

NICHE_ALIASES = {
    "kitchen": {"kitchen", "coffee", "cooking", "food", "appliances", "coffee & espresso"},
    "electronics": {"electronics", "tech", "gadgets", "audio", "computers", "smart home", "phones", "gaming"},
    "home": {"home", "furniture", "cleaning", "garden", "outdoor", "tools", "home improvement", "decor"},
    "fitness": {"fitness", "sports", "health", "outdoor & fitness", "wellness"},
}


def criteria_for(niche: str) -> list[tuple[str, str]]:
    niche = (niche or "").lower()
    for canon, aliases in NICHE_ALIASES.items():
        if niche in aliases:
            return CRITERIA[canon]
    return CRITERIA["general"]


def buying_guide_section(niche: str, keyword: str) -> dict:
    rows = criteria_for(niche)
    return {
        "heading": f"How to choose the right {keyword.lower()} (6 criteria that actually matter)",
        "paragraphs": [
            f"Most {keyword.lower()} buying guides recycle the manufacturer's bullet points. That is not how you avoid a bad purchase. "
            "These are the six things that separate a product you keep using from one that ends up in a cupboard - in the order that matters."
        ],
        "bullets": [f"<strong>{title}.</strong> {body}" for title, body in rows],
    }


def specs_table(items: list[dict], aff_currency: str = "USD") -> str:
    """Build a comparison/spec table from whatever fields the CSV provides."""
    if not items:
        return ""
    single = len(items) == 1
    head = "<th>Specification</th>" + "".join(
        f"<th>{i.get('display_name') or i.get('name', '')}</th>" for i in items[:4]
    )
    fields = [
        ("Best for", "audience"),
        ("Standout feature", "feature"),
        ("Price band", "price"),
        ("Typical rating", "rating"),
        ("ASIN", "asin"),
    ]
    rows = []
    for label, key in fields:
        cells = []
        for item in items[:4]:
            val = (item.get(key) or "").strip()
            if not val:
                val = "-"
            elif key == "price":
                val = money(val.replace(",", "").replace("$", ""), aff_currency) or val
            elif key == "rating":
                val = f"{val} / 5" if val != "-" else "-"
            elif key == "asin":
                val = f"<code>{val}</code>"
            cells.append(f"<td>{val}</td>")
        if all(c == "<td>-</td>" for c in cells):
            continue
        rows.append(f"<tr><th scope=\"row\">{label}</th>{''.join(cells)}</tr>")
    if not rows:
        return ""
    caption = "Product overview" if single else "Side-by-side comparison"
    return (
        f'<div class="table-wrap"><table class="spec-table"><caption>{caption}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def faq_seed(keyword: str, niche: str) -> list[dict]:
    k = keyword.lower()
    return [
        {
            "q": f"Is a more expensive {k} always better?",
            "a": f"Not automatically. With {k} the price usually buys durability, quieter operation and better support rather than a step change in performance. "
                 f"The sensible move is to identify the two or three features you will genuinely use, then buy the cheapest model that has them. "
                 f"Paying for features you will never touch is the most common over-spend in this category.",
        },
        {
            "q": f"What should I check before ordering a {k} online?",
            "a": "Three things: the exact dimensions against your available space, what is included in the box (cables, filters, mounts are often sold separately), "
                 "and the return window. Also confirm the plug type and voltage if you are ordering from a marketplace in another country.",
        },
        {
            "q": f"How long does a {k} typically last?",
            "a": f"Build quality and maintenance decide this more than the brand name. A well maintained {k} in normal home use commonly lasts three to seven years. "
                 "The failure points are almost always consumables and moving parts - so before buying, check that replacements are still sold and at what price.",
        },
        {
            "q": f"Are the deals on {k} real?",
            "a": "Sometimes. Seasonal events genuinely discount this category, but inflated list prices are common. Track the price for two weeks before a big "
                 "shopping event and you will know immediately whether the offer is real. If a discount appears permanent, it is the price.",
        },
    ]


def _fallback_positioning(niche: str, primary: dict) -> str:
    audience = primary.get("audience") or "everyday users"
    return f"a {niche} product aimed at {audience}"


def build_topic_data(topic: dict, cfg: dict) -> dict:
    """Produce a complete article dict offline (no AI required)."""
    aff = cfg.get("affiliate", {})
    keyword = topic["keyword"]
    niche = topic.get("niche", "general")
    template = topic["template"]
    items = topic.get("items", [])
    primary = items[0] if items else {"name": keyword}
    criteria = criteria_for(niche)

    planned_title = (topic.get("title") or "").strip()

    if template == "comparison" and len(items) >= 2:
        a, b = items[0], items[1]
        title = planned_title or f"{a['display_name']} vs {b['display_name']}: Which One Should You Actually Buy?"
        intro = [
            f"Searching for \"{a['name']} vs {b['name']}\" usually lands you on two product descriptions stitched together. "
            f"This is the practical version: what each one is genuinely good at, where each one falls over, and which one fits your situation.",
            f"The comparison focuses on the features, setup choices, day-to-day use and ownership considerations that separate the two options.",
            f"Use the sections below to match the product design and operating approach to the requirements of your own home or project.",
        ]
        sections = [
            {
                "heading": f"The short answer",
                "paragraphs": [
                    f"Buy <strong>{a['display_name']}</strong> if {a.get('audience') or 'you want the safer, more proven option and do not need anything exotic'}. "
                    f"Buy <strong>{b['display_name']}</strong> if {b.get('audience') or 'your priorities are different and the trade-off below matters to you'}.",
                    f"Everything after this point is the reasoning - so you can override that recommendation with your own constraints rather than mine.",
                ],
            },
            {
                "heading": f"What {a['display_name']} does better",
                "bullets": [
                    f"<strong>{criteria[0][0]}.</strong> {criteria[0][1]}",
                    f"<strong>{criteria[1][0]}.</strong> {criteria[1][1]}",
                ],
                "paragraphs": [
                    f"In day to day use this is where {a['display_name']} pulls ahead. It is not a marginal difference either - it is the reason people keep it on the counter "
                    "instead of moving it to storage.",
                ],
            },
            {
                "heading": f"What {b['display_name']} does better",
                "bullets": [
                    f"<strong>{criteria[2][0]}.</strong> {criteria[2][1]}",
                    f"<strong>{criteria[3][0]}.</strong> {criteria[3][1]}",
                ],
                "paragraphs": [
                    f"{b['display_name']} is the better tool for a narrower job, and it does that job properly. If that job is yours, the comparison is over.",
                ],
            },
            buying_guide_section(niche, keyword),
            {
                "heading": "The verdict",
                "paragraphs": [
                    f"For most readers the honest answer is {a['display_name']}: broader usefulness, easier to live with, and a lower chance of buyer's regret in month three.",
                    f"If your needs match the narrower case above, {b['display_name']} is the better spend and you should ignore me.",
                ],
            },
        ]
        pros = [f"{a['display_name']}: stronger on {criteria[0][0].lower()}", f"{b['display_name']}: stronger on {criteria[2][0].lower()}",
                "Both are widely available with established support channels", "Clear price separation makes the decision easier"]
        cons = [f"{a['display_name']} can feel excessive for light use", f"{b['display_name']} is the narrower tool of the two",
                "Neither is a bargain during non-promotional periods"]
    elif template == "bestof":
        title = planned_title or f"Best {keyword}: {len(items)} Options Compared by Real Buying Criteria"
        intro = [
            f"There is no single \"best {keyword.lower()}\" - there is the best one for how you will actually use it. "
            f"So instead of a ranked list pretending objectivity, here are {len(items)} strong options with the trade-offs stated plainly.",
        ]
        openers = [
            "The case for this one is narrow and honest: {focus}. Outside that lane it is merely adequate, which is fine - adequate is not why you are here.",
            "Start with what it refuses to compromise on: {focus}. That single decision shapes everything else about living with it.",
            "This is the pick for people who have already been disappointed once. {focus_cap} is the reason, and it shows up in the first week.",
            "It wins on one axis and is unremarkable on the rest: {focus}. For a lot of buyers that is exactly the right trade.",
            "Ignore the marketing and look at {focus}. That is where the money actually goes, and where cheaper options quietly fail.",
        ]
        closers = [
            "The trade-off: {weak}. If that matters more to you than the strength above, skip it and read the next pick.",
            "Where it disappoints: {weak}. Not a dealbreaker for everyone, but a dealbreaker for the wrong buyer.",
            "The compromise is {weak}. Decide now whether you can live with that for three years, because you will.",
            "Be clear-eyed about {weak}. Everything else here is genuinely good.",
            "Its weak point is {weak}. Everything else here is genuinely good.",
        ]
        picks = []
        used_criteria: set[int] = set()

        def _take_criterion(offset: int):
            idx = offset % len(criteria)
            tries = 0
            while idx in used_criteria and tries < len(criteria):
                idx = (idx + 1) % len(criteria)
                tries += 1
            used_criteria.add(idx)
            return criteria[idx]

        for idx, item in enumerate(items[:5], start=1):
            focus = (item.get("feature") or item.get("audience") or _take_criterion(idx)[0]).strip().rstrip(".")
            weak_label, weak_body = _take_criterion(idx + 2)
            opener = openers[(idx - 1) % len(openers)].format(
                focus=focus.lower(), focus_cap=focus.capitalize()
            )
            closer = closers[(idx + 1) % len(closers)].format(weak=weak_label.lower())
            extra = (
                f"It is worth understanding {weak_label.lower()} before you buy: {weak_body}"
                if idx % 2 == 0
                else f"Nobody mentions this in the listing, and it matters: {weak_body}"
            )
            picks.append(
                {
                    "heading": f"{idx}. {item.get('display_name') or item['name']} - best for {focus.lower()}",
                    "paragraphs": [opener, extra, closer],
                    "bullets": [] if idx % 2 else [
                        f"<strong>Buy it if:</strong> {item.get('audience') or focus.lower()} describes your situation.",
                        f"<strong>Skip it if:</strong> {weak_label.lower()} is something you will notice every single day.",
                    ],
                }
            )
        sections = picks + [
            buying_guide_section(niche, keyword),
            {
                "heading": "So which one should you get?",
                "paragraphs": [
                    "If you want the decision made for you: pick the first option that matches your primary use case and stop reading. "
                    "The gap between these picks is smaller than the gap between buying well and buying twice.",
                ],
            },
        ]
        pros = ["Every pick is justified by a concrete use case", "Price bands covered so the budget is your choice",
                "Trade-offs stated rather than hidden"]
        cons = ["No single winner for every situation", "Availability shifts seasonally"]
    else:  # review
        title = planned_title or f"{keyword} Review: Is It Worth Your Money in 2026?"
        intro = [
            f"Every listing for {keyword.lower()} reads like it was written by the same enthusiastic intern. "
            f"This review takes the opposite approach: what it is genuinely good at, what it is not, and who should skip it entirely.",
            f"The focus is on the product features, controls, construction and ownership details that shape the experience after installation.",
            f"Rather than repeating broad marketing claims, the review explains how the design choices relate to practical residential use.",
        ]
        sections = [
            {
                "heading": f"What {primary.get('display_name') or keyword} actually is",
                "paragraphs": [
                    f"In one line: {primary.get('feature') or _fallback_positioning(niche, primary)}. "
                    "The category is crowded, which is good news for you - competition keeps prices honest and forces brands to fix real problems.",
                    "The bad news is that crowded categories are also where the most overpriced rebrands live. So the details below matter more than the brand name.",
                ],
            },
            {
                "heading": "Where it earns its price",
                "bullets": [
                    f"<strong>{criteria[0][0]}.</strong> {criteria[0][1]}",
                    f"<strong>{criteria[1][0]}.</strong> {criteria[1][1]}",
                    f"<strong>{criteria[2][0]}.</strong> {criteria[2][1]}",
                ],
            },
            {
                "heading": "Where it falls short",
                "paragraphs": [
                    f"No product survives a year of use without an annoyance, and this one has two. First, {criteria[3][1].split('.')[0].lower()}. "
                    f"Second, {criteria[4][1].split('.')[0].lower()}.",
                    "Neither is a dealbreaker on its own. Together they are the reason this is not an unconditional recommendation.",
                ],
            },
            buying_guide_section(niche, keyword),
            {
                "heading": "Who should buy it (and who should not)",
                "paragraphs": [
                    f"Buy it if {primary.get('audience') or 'you want a dependable option and do not enjoy researching'}. "
                    "Skip it if you need something specialist - the category has better narrow tools at similar money.",
                ],
            },
            {
                "heading": "The verdict",
                "paragraphs": [
                    f"Solid, sensible, and slightly less exciting than the marketing suggests. For most buyers of {keyword.lower()}, that is the right combination.",
                    "Check the current price before committing - this category moves more than you would expect between shopping events.",
                ],
            },
        ]
        pros = [
            f"Strong on {criteria[0][0].lower()}",
            f"{criteria[1][0]} is better than the category average",
            "Widely available with a straightforward return policy",
            "Consumables and spare parts are easy to source",
        ]
        cons = [
            f"{criteria[3][0]} is where the cost hides",
            f"{criteria[4][0]} should be verified before you order",
            "Not the specialist choice for advanced users",
        ]

    return {
        "title": title,
        "meta_description": f"{title.split(':')[0]} - an honest, criteria-based breakdown with the trade-offs stated plainly. Updated for 2026.",
        "intro": intro,
        "sections": sections,
        "audience_fit": [
            f"This guide may be useful for buyers whose priorities match the product's main features and intended use in the {niche} category.",
            "It may be less relevant when your requirements call for a specialised feature, installation arrangement or operating method that this product does not provide."
        ],
        "specs": [
            {"label": c[0], "value": c[1]} for c in criteria[:5]
        ],
        "pros": pros,
        "cons": cons,
        "faq": faq_seed(keyword, niche),
        "conclusion": [
            "That is the honest picture. Nothing here is sponsored, and nothing here is a press release wearing a trench coat.",
            f"If {keyword.lower()} fits the use case described above, check the live price and availability before you commit - the numbers in this "
            "category change faster than any article can keep up with.",
        ],
        "_source": "template_engine",
    }
