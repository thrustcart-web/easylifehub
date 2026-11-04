"""One article = one call: plan topic -> write -> render -> gate -> queue/publish."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RAMP_FILE = Path(__file__).resolve().parent.parent / "data" / "ramp.json"


# --------------------------------------------------------------- ramp-up ----
def resolve_ramp(cfg: dict, state: dict) -> tuple[int, int, int]:
    """Growth ramp: the daily rate rises with the age of the blog.

    A brand-new blogspot domain that suddenly publishes 50 posts a day is the
    exact fingerprint Google's "scaled content abuse" policy looks for, and it
    also burns the finite keyword pool in weeks. So the rate starts low and
    climbs on its own - no editing, no intervention.

    Returns (effective_posts_per_day, elapsed_days, hard_cap).
    """
    ramp = cfg.get("ramp") or {}
    hard_cap = int(cfg.get("safety", {}).get("max_posts_per_day_hard_cap", 6))
    base = int(cfg.get("schedule", {}).get("daily_posts", 3))

    if not ramp.get("enabled"):
        return base, 0, hard_cap

    day0 = _ramp_day0(state)
    today = datetime.now(timezone.utc).date()
    elapsed = max(0, (today - day0).days)

    posts = base
    for stage in sorted(ramp.get("stages") or [], key=lambda s: int(s.get("day", 0))):
        if elapsed >= int(stage.get("day", 0)):
            posts = int(stage.get("posts_per_day", posts))

    return max(1, min(posts, hard_cap)), elapsed, hard_cap


def _ramp_day0(state: dict):
    """First day the pipeline ever produced an article (persisted in data/ramp.json)."""
    stored = None
    if RAMP_FILE.exists():
        try:
            stored = json.loads(RAMP_FILE.read_text(encoding="utf-8")).get("day0")
        except (json.JSONDecodeError, OSError):
            stored = None
    if not stored:
        stamps = sorted(str(g.get("ts", "")) for g in state.get("generated", []) if g.get("ts"))
        stored = stamps[0] if stamps else None
    today = datetime.now(timezone.utc)
    if not stored:
        RAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        RAMP_FILE.write_text(json.dumps({"day0": today.strftime("%Y-%m-%d")}, indent=2) + "\n",
                             encoding="utf-8")
        return today.date()
    try:
        return datetime.strptime(stored[:10], "%Y-%m-%d").date()
    except ValueError:
        return today.date()


import render as render_mod
import safety
import template_engine
from affiliate import Affiliate
from ai_writer import AIError, write_article
from images import hero_html
from topic_planner import mark_used, next_topics
from util import (QUEUE_DIR, SEED_DIR, load_config, load_state, log, save_state,
                  setup_logging, utcnow_iso)


def affiliate_tag_ready(cfg: dict) -> bool:
    """True only when a real tracking ID is configured.

    Publishing with the shipped placeholder (YOURTAG-20) produces posts whose
    every Amazon link earns nothing - and those posts then sit on the blog
    looking finished. Failing loudly here is much cheaper than that.
    """
    tag = (cfg.get("affiliate", {}).get("tag") or "").strip()
    return bool(tag) and not tag.startswith("YOURTAG") and " " not in tag


def warn_if_tag_missing(cfg: dict) -> None:
    if affiliate_tag_ready(cfg):
        return
    log.warning("")
    log.warning("=" * 72)
    log.warning("  AFFILIATE TAG IS NOT CONFIGURED")
    log.warning("  Articles will be generated with a placeholder tag and will")
    log.warning("  earn ZERO commission. Fix it in one of two places:")
    log.warning("")
    log.warning("   1) GitHub repo -> Settings -> Secrets and variables -> Actions")
    log.warning("      -> Secrets -> New repository secret")
    log.warning("      Name: AFFILIATE_TAG      Value: yourtag-20")
    log.warning("")
    log.warning("   2) or edit config.json -> affiliate.tag")
    log.warning("=" * 72)
    log.warning("")


def load_seed_articles() -> list[dict]:
    """Hand-written articles shipped with the repo - the day-one backstop.

    Each seed file is {"topic": {...}, "article": {...}} in exactly the same
    shape the AI/template writers produce, so the renderer treats them
    identically (schema.org, TOC, CTAs, disclosure all included).
    """
    out = []
    for path in sorted(SEED_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("bad seed file: %s", path.name)
            continue
        if data.get("topic") and data.get("article"):
            out.append(data)
        else:
            log.warning("seed file %s missing 'topic'/'article' keys", path.name)
    return out


def generate_one(cfg: dict, state: dict, topic: dict, mode: str = "auto") -> dict | None:
    aff = Affiliate(cfg)
    keyword = topic["keyword"]
    niche = topic.get("niche", "")

    # ---- image (cached / offline-safe)
    hero = hero_html(keyword, topic.get("title_hint", keyword), cfg.get("images", {}), niche)

    # ---- write
    article = None
    source = "template"
    if mode in ("auto", "ai"):
        try:
            article, provider = write_article(topic, cfg)
            source = f"ai:{provider}"
        except AIError as exc:
            log.warning("  AI unavailable (%s)", str(exc)[:160])
            if mode == "ai":
                raise
        except Exception as exc:  # noqa: BLE001 - one bad article must never kill the batch
            log.error("  AI writer crashed (%s: %s) - falling back to the template engine",
                      type(exc).__name__, str(exc)[:160])
            if mode == "ai":
                raise AIError(f"{type(exc).__name__}: {exc}") from exc
    if article is None:
        if not cfg.get("content", {}).get("fallback_to_template", True):
            raise AIError("AI failed and template fallback is disabled")
        article = template_engine.build_topic_data(topic, cfg)
        source = "template"
        log.info("  using offline template engine (still a complete, valid article)")

    article["_source"] = source

    # ---- render + gate
    html = render_mod.render_html(article, topic, cfg, aff, hero)
    ok, reasons = safety.quality_gate(html, article, cfg)
    if not ok:
        log.error("  QUALITY GATE REJECTED %s: %s", topic["slug"], "; ".join(reasons[:4]))
        raise AIError("quality gate: " + "; ".join(reasons[:4]))

    path = render_mod.save_article(article, topic, cfg, aff, hero)

    # ---- state
    mark_used(state, topic)
    state.setdefault("generated", []).append(
        {
            "slug": topic["slug"],
            "title": article["title"],
            "template": topic["template"],
            "keyword": keyword,
            "ts": utcnow_iso(),
            "source": source,
            "words": safety.word_count(html),
        }
    )
    if topic["slug"] not in state.setdefault("queued_slugs", []):
        state["queued_slugs"].append(topic["slug"])
    state["counters"]["total_generated"] = state["counters"].get("total_generated", 0) + 1
    return {"slug": topic["slug"], "title": article["title"], "words": safety.word_count(html), "source": source, "path": str(path)}


def run(count: int | None = None, mode: str = "auto", publish: str = "none",
        dry_run: bool = False, use_seed_first: bool = True, verbose: bool = False) -> dict:
    setup_logging(verbose)
    cfg = load_config()
    state = load_state()

    ramp_count, ramp_day, hard_cap = resolve_ramp(cfg, state)
    if count is None:
        count = ramp_count
    else:
        # an explicit --count may exceed the daily ramp, but never the hard cap
        count = max(1, min(int(count), hard_cap))

    log.info("=" * 72)
    log.info("AutoBlog generate | target=%d | mode=%s | publish=%s%s",
             count, mode, publish, " | DRY RUN" if dry_run else "")
    log.info("Ramp: day %d -> %d post(s)/day (hard cap %d)", ramp_day, count, hard_cap)
    log.info("=" * 72)
    warn_if_tag_missing(cfg)

    # seed articles are consumed first so the blog launches with real content
    queued_now = 0
    if use_seed_first:
        seeds = load_seed_articles()
        existing = {g["slug"] for g in state.get("generated", [])}
        for seed in seeds:
            if queued_now >= count:
                break
            topic = seed.get("topic") or {}
            if not topic.get("slug") or topic["slug"] in existing:
                continue
            article = seed["article"]
            aff = Affiliate(cfg)
            hero = hero_html(topic.get("keyword", ""), article["title"], cfg.get("images", {}), topic.get("niche", ""))
            html = render_mod.render_html(article, topic, cfg, aff, hero)
            ok, reasons = safety.quality_gate(html, article, cfg)
            if not ok:
                log.warning("seed %s rejected: %s", topic["slug"], "; ".join(reasons[:3]))
                continue
            render_mod.save_article(article, topic, cfg, aff, hero)
            state.setdefault("generated", []).append(
                {"slug": topic["slug"], "title": article["title"], "template": topic.get("template"),
                 "keyword": topic.get("keyword"), "ts": utcnow_iso(), "source": "seed",
                 "words": safety.word_count(html)}
            )
            state.setdefault("queued_slugs", []).append(topic["slug"])
            state["counters"]["total_generated"] = state["counters"].get("total_generated", 0) + 1
            queued_now += 1
            log.info("  seed article queued: %s", topic["slug"])

    remaining = max(0, count - queued_now)
    made: list[dict] = []
    if remaining:
        topics = next_topics(state, count=remaining)
        if not topics:
            log.warning("topic planner returned nothing - check data/keywords.csv and data/products.csv")
        for topic in topics:
            try:
                result = generate_one(cfg, state, topic, mode=mode)
                if result:
                    made.append(result)
            except Exception as exc:  # noqa: BLE001 - any failure skips this topic only
                log.error("  failed %s (%s): %s", topic["slug"], type(exc).__name__, str(exc)[:200])
                safety.record_failure(cfg, f"{type(exc).__name__}: {exc}")
                continue
            save_state(state)
    else:
        log.info("seed articles filled today's quota")

    # ---- publish (optional; production path is Apps Script)
    published = 0
    if publish == "blogger" and not dry_run:
        published = _publish_now(cfg, state, made)
    elif publish == "blogger":
        log.info("dry run: skipping Blogger publish")

    max_queue = int(cfg.get("schedule", {}).get("max_queue_size", 60))
    removed = render_mod.prune_queue(max_queue) if not dry_run else 0
    render_mod.rebuild_index(cfg)

    # keep the category hub page in sync with whatever is in the repo now
    try:
        import categories
        hub = categories.build_hub(cfg, state)
        if hub:
            log.info("category hub rebuilt: %s", hub)
    except Exception as exc:  # noqa: BLE001 - the hub is a bonus, never fatal
        log.warning("category hub skipped: %s", str(exc)[:160])

    save_state(state)

    summary = {
        "generated": len(made) + queued_now,
        "published": published,
        "queue_size": len(list(QUEUE_DIR.glob("*.json"))) - 1,
        "pruned": removed,
        "ramp_day": ramp_day,
        "ramp_posts_per_day": count,
        "articles": made,
    }
    log.info("-" * 72)
    log.info("DONE | queued=%d | published=%d | queue size=%d", summary["generated"], published, summary["queue_size"])
    return summary


def _publish_now(cfg: dict, state: dict, made: list[dict]) -> int:
    import os

    from blogger import Blogger

    sa_raw = os.environ.get("BLOGGER_SERVICE_ACCOUNT_JSON")
    blog_id = cfg.get("blog", {}).get("id")
    if not sa_raw or not blog_id or str(blog_id).startswith("PUT_YOUR"):
        log.error("publish=blogger needs BLOGGER_SERVICE_ACCOUNT_JSON env var and blog.id in config.json")
        return 0

    closed, reason = safety.breaker_closed(cfg)
    if closed:
        log.error("REFUSING TO PUBLISH: %s", reason)
        return 0

    client = Blogger(sa_raw, blog_id)
    count = 0
    for item in made:
        allowed, why = safety.rate_ok(cfg)
        if not allowed:
            log.warning("rate limiter: %s", why)
            break
        stem = QUEUE_DIR / item["slug"]
        html = Path(str(stem) + ".html").read_text(encoding="utf-8")
        meta = json.loads(Path(str(stem) + ".json").read_text(encoding="utf-8"))
        try:
            post = client.insert_post(meta["title"], html, meta.get("labels", []), is_draft=False)
            state.setdefault("published", []).append(
                {"slug": item["slug"], "post_id": post.get("id"), "ts": utcnow_iso(),
                 "url": post.get("url"), "title": meta.get("title", ""),
                 "niche": meta.get("niche", ""), "words": meta.get("words", 0),
                 "category_label": meta.get("category_label", "")}
            )
            if item["slug"] in state.get("queued_slugs", []):
                state["queued_slugs"].remove(item["slug"])
            safety.record_success(state)
            count += 1
            log.info("  published: %s", post.get("url"))
        except RuntimeError as exc:
            safety.record_failure(cfg, str(exc))
            log.error("  publish failed: %s", str(exc)[:220])
            break
    return count


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="AutoBlog: generate + queue + publish affiliate reviews")
    parser.add_argument("-n", "--count", default=None,
                        help="articles to generate (empty = the automatic growth ramp)")
    parser.add_argument("--mode", choices=["auto", "ai", "template"], default="auto",
                        help="auto = AI with offline fallback (recommended)")
    parser.add_argument("--publish", choices=["none", "blogger"], default="none")
    parser.add_argument("--dry-run", action="store_true", help="generate only, no state/publish side effects on Blogger")
    parser.add_argument("--no-seed", action="store_true", help="skip the bundled hand-written articles")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--ramp", action="store_true",
                        help="print the current growth-ramp stage and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    def _as_count(value):
        """'' -> None (use the ramp); anything else must be a positive int."""
        text = str(value or "").strip()
        if not text:
            return None
        try:
            n = int(text)
        except ValueError:
            raise SystemExit(f"--count must be a number or empty, got: {value!r}")
        return n if n > 0 else None

    if args.ramp:
        from topic_planner import stats as tp_stats

        cfg = load_config()
        st = load_state()
        n, day, cap = resolve_ramp(cfg, st)
        s = tp_stats()
        print(json.dumps({
            "day": day,
            "posts_per_day": n,
            "hard_cap": cap,
            "ramp_enabled": bool((cfg.get("ramp") or {}).get("enabled")),
            "stages": (cfg.get("ramp") or {}).get("stages", []),
            "keyword_rows": s.get("keyword_rows"),
            "niches": s.get("niches"),
            "years_of_content_at_current_rate": round((s.get("keyword_rows") or 0) / (n * 365), 1) if n else 0,
        }, indent=2))
        return 0

    if args.stats:
        from topic_planner import stats
        print(json.dumps(stats(), indent=2))
        return 0

    run(count=_as_count(args.count), mode=args.mode, publish=args.publish,
        dry_run=args.dry_run, use_seed_first=not args.no_seed, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main(sys.argv[1:]))
