"""Shared helpers: config loading, state, slugs, logging, retry.

Standard library only - no external dependencies. This keeps the project free
and runnable anywhere (GitHub Actions, local machine, containers).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
QUEUE_DIR = ROOT / "content" / "queue"
SEED_DIR = ROOT / "content" / "seed"

log = logging.getLogger("autoblog")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


# ---------------------------------------------------------------- config ----
def _strip_comment_keys(obj):
    """Remove keys starting with '_' (inline documentation) recursively."""
    if isinstance(obj, dict):
        return {k: _strip_comment_keys(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comment_keys(v) for v in obj]
    return obj


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    # environment overrides (used by CI secrets so config.json can stay public)
    env_map = {
        "BLOG_ID": ("blog", "id"),
        "BLOGGER_BLOG_ID": ("blog", "id"),
        "AFFILIATE_TAG": ("affiliate", "tag"),
        "AMAZON_TAG": ("affiliate", "tag"),
        "DAILY_POSTS": ("schedule", "daily_posts"),
    }
    for env_name, (section, key) in env_map.items():
        val = os.environ.get(env_name)
        if val:
            cfg.setdefault(section, {})
            cfg[section][key] = int(val) if key == "daily_posts" else val
    return _strip_comment_keys(cfg)


# ----------------------------------------------------------------- state ----
def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("state.json corrupted - starting fresh (backup kept)")
            STATE_PATH.rename(STATE_PATH.with_suffix(".corrupt.json"))
    return {
        "generated": [],          # [{"slug","title","template","keyword","ts","source"}]
        "published": [],          # [{"slug","post_id","ts","url"}]
        "queued_slugs": [],       # slugs waiting in content/queue
        "keywords_used": {},      # keyword -> last used ISO date
        "last_publish_ts": None,
        "failures": {"count": 0, "until": None, "last_error": None},
        "counters": {"total_generated": 0, "total_published": 0},
    }


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE_PATH)


# ------------------------------------------------------------------ misc ----
def slugify(text: str, max_len: int = 70) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:max_len].rstrip("-") or "post"


def stable_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def read_csv_rows(path: Path) -> list[dict]:
    """Tiny CSV reader.

    Tolerates blank lines, `#` comment lines, ragged rows (csv.DictReader puts
    the overflow into a list under the None key) and BOM headers.
    """
    import csv

    if not path.exists():
        return []

    rows: list[dict] = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(line for line in fh if not line.lstrip().startswith("#"))
        for raw in reader:
            clean = {}
            for key, value in raw.items():
                if key is None:
                    continue
                if isinstance(value, list):
                    value = " ".join(str(v) for v in value if v)
                clean[str(key).strip()] = str(value or "").strip()
            if any(clean.values()):
                rows.append(clean)
    return rows


def jitter(seconds_range: tuple[int, int]) -> None:
    """Sleep a random amount - avoids a machine-perfect posting fingerprint."""
    delay = random.uniform(*seconds_range)
    log.info("humanising delay: sleeping %.1fs", delay)
    time.sleep(delay)


def word_count(html: str) -> int:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;", " ", text)
    return len([w for w in text.split() if len(w) > 1])


def money(amount, currency: str = "USD") -> str:
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "EGP": "E£", "SAR": "SAR "}
    sym = symbols.get(currency, currency + " ")
    try:
        return f"{sym}{float(amount):,.0f}"
    except (TypeError, ValueError):
        return ""
