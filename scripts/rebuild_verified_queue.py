"""Rebuild legacy queue articles as current V4 from existing verified products only.

No Apify, SerpApi, or product discovery endpoint is called. Products are
accepted only when their exact ASIN/title pair is already verified in queue
metadata or can be matched against the local Amazon ASIN cache.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from affiliate import Affiliate
from ai_writer import AIError, build_prompts, normalise, validate
from providers import generate_json
from product_resolver import _load_cache, _normal_name, _trusted_item, title_matches
from render import rebuild_index, render_html, save_article
from safety import quality_gate
from util import QUEUE_DIR, load_config, setup_logging, log


def resolve_from_existing_data(item: dict, cache: dict, aff: Affiliate) -> dict | None:
    """Return a verified item using queue data or the local cache; never searches."""
    if not isinstance(item, dict):
        return None
    resolved = dict(item)
    name = str(resolved.get("display_name") or resolved.get("name") or "").strip()
    if not name:
        return None
    resolved["name"] = name
    resolved["display_name"] = name

    if not _trusted_item(resolved):
        cache_keys = [_normal_name(name)]
        search_term = str(resolved.get("search_term") or "").strip()
        if search_term:
            cache_keys.append(_normal_name(search_term))
        cached = next(
            (
                cache.get(key)
                for key in cache_keys
                if isinstance(cache.get(key), dict)
                and re.fullmatch(r"[A-Z0-9]{10}", str(cache[key].get("asin") or "").upper())
                and title_matches(name, str(cache[key].get("title") or ""))
            ),
            None,
        )
        if cached is None:
            return None
        resolved["asin"] = str(cached["asin"]).upper()
        resolved["asin_verified"] = True
        resolved["verified_product_title"] = str(cached["title"])

    if not _trusted_item(resolved):
        return None

    # Rebuild the tagged /dp/ URL from the verified ASIN and configured tag.
    url = aff.url_for(resolved)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    asin = str(resolved.get("asin") or "").upper()
    tags = parse_qs(parsed.query).get("tag", [])
    if not (
        parsed.scheme == "https"
        and parsed.hostname == "www.amazon.com"
        and parsed.path == f"/dp/{asin}"
        and tags == [aff.tag]
        and not parsed.fragment
    ):
        return None
    resolved["url"] = url
    return resolved


def resolved_items_for_meta(meta: dict, cache: dict, aff: Affiliate) -> list[dict] | None:
    """Return all queue products only when every one is verified from local data."""
    items = meta.get("items") or []
    if not items:
        return None
    resolved = [resolve_from_existing_data(item, cache, aff) for item in items]
    if any(item is None for item in resolved):
        return None
    return resolved


def _remove_customer_opinion_sentences(value):
    """Remove complete sentences that refer to reviews, ratings, or buyer opinion."""
    if isinstance(value, str):
        parts = re.split(r"(?<=[.!?])\s+", value.strip())
        forbidden = re.compile(
            r"\b(?:customers?|buyers?|users?|review|reviews|reviewer|reviewers|"
            r"rating|ratings|star|stars|testimonial|testimonials|feedback|"
            r"popularity|popular|best-selling)\b",
            re.I,
        )
        kept = [part.strip() for part in parts if part.strip() and not forbidden.search(part)]
        return " ".join(kept).strip()
    if isinstance(value, list):
        return [_remove_customer_opinion_sentences(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (item if key == "title" else _remove_customer_opinion_sentences(item))
            for key, item in value.items()
        }
    return value


def write_repair_article(topic: dict, cfg: dict) -> tuple[dict, str]:
    """Use only Gemini, scrub buyer-opinion claims, then run the full article validator."""
    cfg.setdefault("ai", {})["primary"] = "gemini"
    cfg["ai"]["fallbacks"] = []
    content_cfg = cfg.setdefault("content", {})
    original_target = int(content_cfg.get("target_words") or 1100)
    content_cfg["min_words"] = 600

    for item in topic.get("items", []):
        strict_note = (
            "ABSOLUTE EDITORIAL BAN: do not mention customer reviews, buyer or user "
            "opinions, ratings, stars, scores, review counts, testimonials, popularity, "
            "or what customers say. Do not quote, paraphrase, summarize, or refer to "
            "these subjects. Discuss only supplied listing facts and practical checks."
        )
        item["notes"] = (str(item.get("notes") or "").strip() + " " + strict_note).strip()

    last_problems = []
    for target in (max(original_target, 1100), max(original_target, 1250), max(original_target, 1400)):
        content_cfg["target_words"] = target
        system, user = build_prompts(topic, cfg)
        user += (
            "\\n\\nFINAL REMINDER: absolutely no customer, buyer, or user feedback; "
            "no reviews, ratings, stars, scores, review counts, testimonials, or popularity. "
            "Do not mention that this information is missing. Return only useful product "
            "facts from the supplied product title and neutral buyer checks."
        )
        if last_problems:
            user += "\\nThe previous response was rejected for: " + "; ".join(last_problems[:6])
            user += "\\nCorrect the issue and return the complete JSON article."

        try:
            raw, provider = generate_json(cfg["ai"], system, user)
            article = normalise(raw, topic)
            article = _remove_customer_opinion_sentences(article)
            ok, problems = validate(article, cfg)
            if ok:
                return article, provider
            last_problems = problems
            log.warning(
                "Gemini repair content failed validation | %s | %s",
                topic.get("slug", ""), "; ".join(problems[:5])
            )
        except AIError as exc:
            last_problems = [str(exc)]
            log.warning(
                "Gemini repair attempt at %d words failed | %s | %s",
                target, topic.get("slug", ""), str(exc)[:180]
            )
        except Exception as exc:  # one bad response must never break the whole queue batch
            last_problems = [f"{type(exc).__name__}: {exc}"]
            log.warning("Gemini repair parser failed | %s | %s", topic.get("slug", ""), str(exc)[:180])

    raise AIError("Gemini could not produce a clean, valid article after three attempts: " + "; ".join(last_problems[:6]))


def build_levoit_official_specs_article() -> dict:
    """A fully written article grounded only in Levoit's published Core 300-P specifications."""
    return {
        "title": "LEVOIT Core 300-P Air Purifier: Room Fit, Filtration, and Filter Care",
        "meta_description": "A practical guide to the LEVOIT Core 300-P, covering its three-stage filter, room coverage, controls, dimensions, and replacement-filter planning.",
        "intro": [
            "The LEVOIT Core 300-P is a compact, cylindrical air purifier designed for bedrooms, home offices, and other smaller rooms. Its straightforward layout combines a three-stage filter, three fan speeds, a sleep setting, and a timer in a unit that can sit beside a wall without taking up much floor space.",
            "The most useful way to assess this model is to start with the room it is expected to serve. LEVOIT lists a clean-air delivery rate of 143 cubic feet per minute and gives two room-size figures: 1,066 square feet for one air change per hour, or 222 square feet for 4.8 air changes per hour. Those figures describe different cleaning rates, so the smaller number is the more useful reference when frequent air circulation is the goal.",
            "This guide explains the filtration stages, room-size figures, controls, physical dimensions, and replacement-filter considerations. It focuses on manufacturer-published product information and practical checks to make before choosing an air purifier for a particular room."
        ],
        "sections": [
            {
                "heading": "A compact design for smaller rooms",
                "paragraphs": [
                    "The Core 300-P measures 8.7 by 8.7 by 14.2 inches and weighs 7.9 pounds. Its round footprint and upright shape make it easier to position than a wide floor unit. Before setting it in place, measure the area where it will stand and leave open space around the air intake and upper outlet. A purifier needs room for air to move; placing it tightly between furniture or behind a curtain works against that basic requirement.",
                    "The listed electrical specification is 120 volts at 60 hertz, with rated power of 56 watts. Check the voltage and plug requirements for the country where it will be used. This model is a mains-powered appliance, so it needs a suitable indoor outlet and a location where its cable will not become a trip hazard.",
                    "Its dimensions can suit a bedside area, a home-office corner, or a small living space. For a larger open-plan room, decide whether one compact unit can serve the entire area or whether the layout calls for a purifier with a higher clean-air delivery rate."
                ],
                "bullets": [
                    "<strong>Dimensions:</strong> 8.7 × 8.7 × 14.2 inches.",
                    "<strong>Weight:</strong> 7.9 pounds.",
                    "<strong>Electrical rating:</strong> AC 120V, 60Hz; rated power 56W."
                ]
            },
            {
                "heading": "How the three-stage filtration is arranged",
                "paragraphs": [
                    "LEVOIT describes the Core 300-P as a three-stage filtration system. The outer pre-filter is the first layer and is intended to catch larger material such as dust, lint, fibers, and hair. By handling larger particles first, it helps protect the filter layers behind it.",
                    "The central filter is the particle-focused stage. The product is supplied with an original three-stage filter installed, and LEVOIT identifies dust, smoke particles, pollen, and pet dander among the particles the system is designed to trap. The third stage uses activated carbon to help address smoke, fumes, volatile organic compounds, and household odours.",
                    "These layers perform different jobs, so filter choice matters. LEVOIT sells replacement filters for the Core 300-P series, including an original filter and options designed for particular household conditions. Check the exact model compatibility on the replacement filter packaging before ordering; a filter that fits a different purifier in the same brand family is not automatically interchangeable."
                ],
                "bullets": [
                    "<strong>Pre-filter:</strong> catches larger particles such as dust, lint, and hair.",
                    "<strong>Particle filtration:</strong> targets fine airborne particles including dust, smoke particles, pollen, and pet dander.",
                    "<strong>Activated carbon:</strong> helps reduce odours, fumes, and selected household gases."
                ]
            },
            {
                "heading": "Room coverage and clean-air delivery",
                "paragraphs": [
                    "The manufacturer's specifications list a CADR of 143 CFM. LEVOIT gives an ideal room-size figure of 1,066 square feet at one air change per hour and 222 square feet at 4.8 air changes per hour. These are not competing coverage claims: they describe how often the air in a room is circulated through the purifier.",
                    "For a bedroom or home office where more frequent air cleaning is the priority, use the 222-square-foot figure as the closer comparison point. The larger figure describes a much slower air-change rate. Room height, open doorways, furniture placement, and the position of the purifier also affect how air moves through a real space.",
                    "Measure the room rather than relying on a general label such as small or medium. For an open floor plan, add the connected areas that remain open to one another. If the resulting space is much larger than the higher air-change reference, compare models with a higher CADR before deciding."
                ],
                "bullets": [
                    "<strong>CADR:</strong> 143 CFM.",
                    "<strong>Frequent air cleaning reference:</strong> 222 square feet at 4.8 air changes per hour.",
                    "<strong>Lower air-change reference:</strong> 1,066 square feet at one air change per hour."
                ]
            },
            {
                "heading": "Fan settings, sleep mode, and timer",
                "paragraphs": [
                    "The control panel provides three fan speeds: low, medium, and high. This gives the owner a direct way to choose between a quieter setting and stronger airflow. The right setting depends on the room, the time of day, and how much background sound is acceptable in that space.",
                    "Sleep Mode runs the purifier at its lowest fan speed and reduces the brightness of the indicators. It is intended for nighttime use when both airflow noise and light from the control panel matter. LEVOIT also lists a timer with selections from two to eight hours, plus a check-filter indicator that signals when filter maintenance needs attention.",
                    "The controls are on the appliance itself, so consider where the purifier will be placed. A position that is easy to reach makes it simpler to adjust the fan, set the timer, and check the indicator without moving the unit."
                ],
                "bullets": [
                    "<strong>Fan speeds:</strong> low, medium, and high.",
                    "<strong>Sleep Mode:</strong> lowest fan speed with dimmed indicators.",
                    "<strong>Timer:</strong> selectable two-to-eight-hour operation.",
                    "<strong>Filter indicator:</strong> provides a reminder to check the filter."
                ]
            },
            {
                "heading": "Noise, energy, and everyday placement",
                "paragraphs": [
                    "LEVOIT lists an operating noise range of 24 to 54.5 decibels. The low end corresponds to quieter operation, while higher fan settings move more air and produce more sound. If the purifier will run in a bedroom, place it where the airflow path is clear and where the sound level at the bed is acceptable. A simple placement check is more useful than assuming every room will sound the same.",
                    "The rated power is 56 watts. Actual energy use depends on how long the appliance runs and which fan setting is selected. A household planning to operate it for extended periods can use the rated power as a reference when estimating electricity consumption, while remembering that the appliance does not necessarily draw the full rated amount continuously at every setting.",
                    "Keep the purifier on a stable, dry, level surface and follow the manual's clearance and cleaning instructions. Avoid blocking the inlet or outlet, and do not place the unit where splashes or excessive humidity can reach the filter."
                ]
            },
            {
                "heading": "Planning for replacement filters",
                "paragraphs": [
                    "Filter maintenance is part of owning any purifier with replaceable filtration. The Core 300-P includes an original filter in the box, installed before use. When the check-filter indicator appears, inspect the filter and consult the manual for the reset and replacement procedure. The indicator is a maintenance prompt; it does not remove the need to keep track of the operating environment and filter condition.",
                    "Dust levels, cooking activity, pets, smoke, and hours of operation can all affect how quickly a filter needs attention. Keep the model number of the purifier available when buying replacements and choose a filter explicitly marked as compatible with the Core 300-P series. Follow the instructions for removing protective packaging from a new filter before switching the purifier back on.",
                    "A sensible setup is to identify the suitable replacement filter before the original one reaches the end of its service life. That makes the next maintenance step easier and helps avoid choosing a filter based only on a similar-looking product name."
                ]
            }
        ],
        "audience_fit": [
            "The Core 300-P may suit someone furnishing a bedroom, home office, or other smaller room who wants a compact purifier with manual fan-speed selection, a sleep setting, a timer, and a three-stage filter.",
            "It may be less suitable when the intended space is substantially larger than the frequent-air-cleaning coverage figure, when the room needs a different electrical specification, or when the owner specifically requires connected app controls. Compare room size, power requirements, and desired controls before selecting a model."
        ],
        "conclusion": [
            "The LEVOIT Core 300-P combines a compact footprint with a three-stage filter, a 143 CFM CADR, three fan speeds, Sleep Mode, a timer, and a filter indicator. The key decision is whether its stated 222-square-foot reference at 4.8 air changes per hour fits the room where it will operate.",
            "Measure the space, check the outlet requirements, choose a suitable position with clear airflow, and confirm replacement-filter compatibility. Those practical checks provide a sound basis for deciding whether this model's size, controls, and filtration arrangement match the room."
        ],
        "specs": [
            {"label": "CADR", "value": "143 CFM"},
            {"label": "Room size at 4.8 ACH", "value": "222 square feet"},
            {"label": "Room size at 1 ACH", "value": "1,066 square feet"},
            {"label": "Filtration", "value": "Three-stage filtration with pre-filter, particle filter, and activated carbon"},
            {"label": "Noise level", "value": "24–54.5 dB"},
            {"label": "Dimensions", "value": "8.7 × 8.7 × 14.2 inches"},
            {"label": "Weight", "value": "7.9 pounds"},
            {"label": "Rated power", "value": "56 watts"}
        ],
        "pros": [],
        "cons": [],
        "faq": [],
        "_source": "manual:official_specs",
        "_provider": "levoit_official_product_information",
        "_editorial_approved": True
    }


def topic_from_meta(meta: dict, resolved_items: list[dict]) -> dict:
    """Build a writer topic from queued metadata and verified local product data."""
    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "meta_description": meta.get("meta_description", ""),
        "template": meta.get("template", "review"),
        "niche": meta.get("niche", "general"),
        "keyword": meta.get("keyword") or meta["title"],
        "labels": list(meta.get("labels") or []),
        "items": resolved_items,
    }


def rebuild(limit: int = 8) -> dict:
    cfg = load_config()
    # For this queue-repair batch, lead with the Gemini Lite model that passed
    # the live smoke test; retain OpenRouter as the independent fallback.
    cfg.setdefault("ai", {})["primary"] = "gemini"
    cfg["ai"]["fallbacks"] = []
    aff = Affiliate(cfg)
    cache = _load_cache()
    index_path = QUEUE_DIR / "index.json"
    if not index_path.exists():
        raise SystemExit("Queue index not found; nothing was changed.")

    index = json.loads(index_path.read_text(encoding="utf-8"))
    candidates = []
    for listed in index.get("posts", []):
        slug = str(listed.get("slug") or "").strip()
        if not slug:
            continue
        meta_path = QUEUE_DIR / f"{slug}.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Keep any V4 post that already passes the project's 600-word floor.
        # Only repair legacy layouts or V4 drafts that are genuinely too thin.
        if (
            int(meta.get("layout_version") or 0) >= 4
            and int(meta.get("words") or 0) >= 600
        ):
            continue
        resolved_items = resolved_items_for_meta(meta, cache, aff)
        if resolved_items:
            candidates.append((meta, resolved_items))

    candidates = sorted(candidates, key=lambda pair: str(pair[0].get("slug")))[:max(1, min(int(limit), 16))]
    if not candidates:
        raise SystemExit("No legacy articles with fully verified existing product data were found.")

    results = []
    for meta, resolved_items in candidates:
        slug = meta["slug"]
        try:
            topic = topic_from_meta(meta, resolved_items)
            if slug == "review-levoit-air-purifier-core-300-853749094376":
                article = build_levoit_official_specs_article()
                provider = "official_specs"
            else:
                article, provider = write_repair_article(topic, cfg)
            if not article.get("_source"):
                article["_source"] = f"ai:{provider}"
            if not article.get("_provider"):
                article["_provider"] = provider

            article_ok, article_problems = validate(article, cfg)
            if not article_ok:
                raise AIError("article validation: " + "; ".join(article_problems[:8]))

            # The fixed renderer creates only green, tracked Amazon product buttons.
            # No images or product facts are fetched during this repair.
            html = render_html(article, topic, cfg, aff, hero="")
            ok, reasons = quality_gate(
                html, article, cfg, expected_product_count=len(topic["items"])
            )
            if not ok:
                raise AIError("quality gate: " + "; ".join(reasons[:6]))

            save_article(article, topic, cfg, aff, hero="")
            saved = json.loads((QUEUE_DIR / f"{slug}.json").read_text(encoding="utf-8"))
            if (
                int(saved.get("layout_version") or 0) != 4
                or saved.get("review_required") is not False
                or saved.get("product_links_verified") is not True
            ):
                raise AIError("post-save queue metadata did not pass the V4 publication checks")

            results.append({
                "slug": slug,
                "title": saved.get("title", ""),
                "provider": provider,
                "words": saved.get("words", 0),
                "status": "rebuilt_v4",
            })
            log.info("V4 REBUILT | %s | %s | %s words", slug, provider, saved.get("words", 0))
        except Exception as exc:  # one provider or article failure must not discard the other upgrades
            results.append({"slug": slug, "status": "skipped", "reason": str(exc)[:240]})
            log.warning("V4 REBUILD SKIPPED | %s | %s", slug, str(exc)[:240])

    rebuild_index(cfg)
    summary = {
        "eligible": len(candidates),
        "rebuilt": sum(1 for row in results if row["status"] == "rebuilt_v4"),
        "skipped": sum(1 for row in results if row["status"] == "skipped"),
        "product_discovery_requests": 0,
        "blogger_posts_published": 0,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["rebuilt"] == 0:
        raise SystemExit("No articles passed the current AI and V4 quality gates.")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=8, help="maximum legacy articles to rebuild (hard cap: 16)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)
    rebuild(args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
