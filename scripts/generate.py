"""One article = one call: plan topic -> write -> render -> gate -> queue/publish."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import render as render_mod
import safety
import template_engine
from affiliate import Affiliate
from ai_writer import AIError, write_article
from images import hero_html
from topic_planner import mark_used, next_topics
from util import (QUEUE_DIR, SEED_DIR, load_config, load_state, log, save_state,
                  setup_logging, utcnow_iso)


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
    count = count or int(cfg.get("schedule", {}).get("daily_posts", 3))

    log.info("=" * 72)
    log.info("AutoBlog generate | target=%d | mode=%s | publish=%s%s",
             count, mode, publish, " | DRY RUN" if dry_run else "")
    log.info("=" * 72)

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
            except (AIError, RuntimeError) as exc:
                log.error("  failed %s: %s", topic["slug"], str(exc)[:200])
                safety.record_failure(cfg, str(exc))
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
    save_state(state)

    summary = {
        "generated": len(made) + queued_now,
        "published": published,
        "queue_size": len(list(QUEUE_DIR.glob("*.json"))) - 1,
        "pruned": removed,
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
                {"slug": item["slug"], "post_id": post.get("id"), "ts": utcnow_iso(), "url": post.get("url")}
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
    parser.add_argument("-n", "--count", type=int, default=None, help="articles to generate (default: config daily_posts)")
    parser.add_argument("--mode", choices=["auto", "ai", "template"], default="auto",
                        help="auto = AI with offline fallback (recommended)")
    parser.add_argument("--publish", choices=["none", "blogger"], default="none")
    parser.add_argument("--dry-run", action="store_true", help="generate only, no state/publish side effects on Blogger")
    parser.add_argument("--no-seed", action="store_true", help="skip the bundled hand-written articles")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.stats:
        from topic_planner import stats
        print(json.dumps(stats(), indent=2))
        return 0

    run(count=args.count, mode=args.mode, publish=args.publish,
        dry_run=args.dry_run, use_seed_first=not args.no_seed, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main(sys.argv[1:]))
