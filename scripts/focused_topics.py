"""Curated, evidence-led long-form topics for the monitor-arm search cluster."""
from __future__ import annotations

import copy
import json

import util


RESEARCH_NOTES = (
    "General category guidance, not a claim about the selected Amazon product: OSHA workstation "
    "guidance recommends placing the screen directly in front of the user, keeping the top of the "
    "screen at or slightly below eye level, and choosing a comfortable viewing distance generally "
    "around 20 to 40 inches. The centre of the screen is commonly viewed slightly below horizontal. "
    "VESA FDMI defines standardized display-mount interfaces; 75 x 75 mm and 100 x 100 mm are common "
    "patterns, but never assume a particular monitor supports either pattern without checking its "
    "manual. Arm load limits, desk-thickness ranges, clamp clearances, grommet sizes, reach, and "
    "supported screen dimensions are model-specific and must be checked against the exact manual."
)


MONITOR_ARM_TOPICS = [
    {
        "slug": "monitor-arm-buying-guide-vesa-weight-desk-fit",
        "title": "Monitor Arm Buying Guide: VESA Fit, Weight Capacity, Desk Clamp, and Reach",
        "keyword": "monitor arm buying guide",
        "template": "review",
        "niche": "home office",
        "family": "monitor arm",
        "subject": "monitor arm buying guide vesa weight desk fit",
        "content_profile": "monitor_arm_longform",
        "editorial_brief": (
            "Write a genuinely useful category buying guide, not a shallow product review. Organize six "
            "detailed sections around: (1) checking the monitor's actual weight without its original stand; "
            "(2) confirming VESA hole pattern, screw depth, recessed mounting areas, and when an adapter may "
            "be needed; (3) matching the monitor to the arm's minimum and maximum supported load, not just "
            "the advertised screen diagonal; (4) checking clamp versus grommet mounting, desktop thickness, "
            "desk apron, underside obstructions, material, and reinforcement; (5) measuring reach, height, "
            "desk depth, wall clearance, tilt, swivel, rotation, and cable slack; and (6) a careful installation "
            "and adjustment checklist. Explain why curved and ultrawide screens can create extra leverage. "
            "Never invent a universal weight, size, VESA, or desk-thickness limit."
        ),
        "items": [{
            "name": "Monitor Arm",
            "display_name": "Monitor Arm",
            "search_term": "monitor arm",
            "niche": "home office",
            "notes": RESEARCH_NOTES,
        }],
        "labels": ["Reviews", "Home Office"],
    },
    {
        "slug": "monitor-arm-ergonomic-setup-screen-height-viewing-distance",
        "title": "How to Set Up a Monitor Arm: Screen Height, Viewing Distance, and Desk Position",
        "keyword": "how to set up a monitor arm",
        "template": "review",
        "niche": "home office",
        "family": "monitor arm",
        "subject": "monitor arm ergonomic setup screen height viewing distance",
        "content_profile": "monitor_arm_longform",
        "editorial_brief": (
            "Write a practical setup guide for a real desk, using six detailed sections: prepare chair, "
            "keyboard, mouse, and seated posture before moving the screen; set viewing distance without "
            "leaning toward the display; set vertical position using the top edge and natural downward gaze; "
            "centre the screen and manage tilt, swivel, rotation, and window glare; position two screens "
            "according to which one is used most; and complete a step-by-step final fit check after tightening "
            "the arm and routing cables. Explain OSHA's general monitor-position guidance in plain language, "
            "but do not promise that a monitor arm treats or prevents a medical condition. Emphasize that "
            "comfort, vision, monitor size, and corrective lenses can change the final position."
        ),
        "items": [{
            "name": "Monitor Arm",
            "display_name": "Monitor Arm",
            "search_term": "monitor arm",
            "niche": "home office",
            "notes": RESEARCH_NOTES,
        }],
        "labels": ["Reviews", "Home Office"],
    },
    {
        "slug": "dual-monitor-arm-guide-load-vesa-alignment-desk-space",
        "title": "Dual Monitor Arm Guide: Screen Fit, Weight Capacity, Alignment, and Desk Space",
        "keyword": "dual monitor arm buying guide",
        "template": "review",
        "niche": "home office",
        "family": "monitor arm",
        "subject": "dual monitor arm guide load vesa alignment desk space",
        "content_profile": "monitor_arm_longform",
        "editorial_brief": (
            "Create a detailed decision guide for a two-screen desk. Use six sections: shared crossbar "
            "systems versus independently adjustable arms; checking each monitor's VESA pattern and actual "
            "weight against the correct per-screen or combined load rating; comparing screen widths, bezels, "
            "depth, curve, portrait rotation, and where the VESA mount sits; checking desk strength, clamp "
            "position, grommet options, thickness and underside clearance; choosing side-by-side, angled, or "
            "stacked layouts based on which screen is primary; and routing cables while preserving movement "
            "and a comfortable viewing position. Explain that a dual label or an advertised screen-size range "
            "does not prove compatibility. Do not invent product specifications."
        ),
        "items": [{
            "name": "Dual Monitor Arm",
            "display_name": "Dual Monitor Arm",
            "search_term": "dual monitor arm",
            "niche": "home office",
            "notes": RESEARCH_NOTES,
        }],
        "labels": ["Reviews", "Home Office"],
    },
    {
        "slug": "ultrawide-monitor-arm-guide-weight-vesa-reach-clearance",
        "title": "Ultrawide Monitor Arm Guide: Weight, VESA Compatibility, Reach, and Desk Clearance",
        "keyword": "ultrawide monitor arm compatibility guide",
        "template": "review",
        "niche": "home office",
        "family": "monitor arm",
        "subject": "ultrawide monitor arm guide weight vesa reach clearance",
        "content_profile": "monitor_arm_longform",
        "editorial_brief": (
            "Write a careful guide for people considering an arm for an ultrawide or curved display. Use "
            "six detailed sections: why diagonal size alone is not enough; how to check the monitor's real "
            "weight and the arm's supported load range; how screen depth, curvature, VESA location, and "
            "accessories affect leverage and balance; how to verify desk clamp or grommet fit and desktop "
            "strength; how to measure reach, height, wall clearance, rotation and cable movement; and what "
            "to inspect during installation and tension adjustment. Distinguish between a manufacturer's "
            "stated compatibility and a buyer's own measurements. Do not assume every ultrawide uses the "
            "same VESA pattern or weight."
        ),
        "items": [{
            "name": "Ultrawide Monitor Arm",
            "display_name": "Ultrawide Monitor Arm",
            "search_term": "ultrawide monitor arm",
            "niche": "home office",
            "notes": RESEARCH_NOTES,
        }],
        "labels": ["Reviews", "Home Office"],
    },
]


def monitor_arm_topics(state: dict | None = None, count: int = 4) -> list[dict]:
    """Return the remaining curated monitor-arm topics without duplicating queue/Blogger titles."""
    state = state or {}
    seen_slugs = {
        str(row.get("slug") or "")
        for row in state.get("generated", [])
        if isinstance(row, dict)
    }
    seen_slugs.update(str(x) for x in (state.get("queued_slugs") or []))
    seen_titles = {
        str(row.get("title") or "").strip().casefold()
        for row in state.get("generated", [])
        if isinstance(row, dict) and row.get("title")
    }
    seen_titles.update(
        str(title or "").strip().casefold()
        for title in (state.get("blogger_titles") or [])
        if str(title or "").strip()
    )

    queue_dir = util.QUEUE_DIR
    if queue_dir.exists():
        for meta_path in queue_dir.glob("*.json"):
            if meta_path.name in {"index.json", "product-audit.json"}:
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            slug = str(meta.get("slug") or meta_path.stem)
            title = str(meta.get("title") or "").strip()
            seen_slugs.add(slug)
            if title:
                seen_titles.add(title.casefold())

    result = []
    for spec in MONITOR_ARM_TOPICS:
        if spec["slug"] in seen_slugs or spec["title"].casefold() in seen_titles:
            continue
        result.append(copy.deepcopy(spec))
        if len(result) >= max(0, int(count)):
            break
    return result
