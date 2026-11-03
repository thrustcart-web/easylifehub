#!/usr/bin/env python3
"""Entry point. Everything lives in scripts/generate.py.

  python3 main.py --dry-run -n 3            # generate 3 articles, publish nothing
  python3 main.py --mode template -n 5      # force the offline engine (no API keys)
  python3 main.py -n 3 --publish blogger    # generate + push straight to Blogger
  python3 main.py --stats                   # what has been generated/published
  python3 main.py --verify                  # check Blogger credentials & permissions
  python3 main.py --preview                 # render the queue as a local blog site
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))


def verify() -> int:
    import os

    from blogger import verify_setup
    from util import load_config

    cfg = load_config()
    sa = os.environ.get("BLOGGER_SERVICE_ACCOUNT_JSON")
    if not sa:
        print("ERROR: set BLOGGER_SERVICE_ACCOUNT_JSON to the contents of your service-account .json file")
        return 1
    blog_id = cfg.get("blog", {}).get("id", "")
    if not blog_id or blog_id.startswith("PUT_YOUR"):
        print("ERROR: set blog.id in config.json (or the BLOG_ID env var)")
        return 1
    try:
        result = verify_setup(sa, blog_id)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        print("\nMost common causes:")
        print("  1. The service-account email was not added as an Author on the blog")
        print("     (Blogger dashboard -> Settings -> Permissions -> Invite more users)")
        print("  2. Blogger API v3 is not enabled in the Google Cloud project")
        print("  3. Wrong blog ID")
        return 1
    print(json.dumps(result, indent=2))
    print("\nSetup verified - the pipeline can post to this blog.")
    return 0


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()

    from generate import main as generate_main

    return generate_main([a for a in argv if a != "--verify"])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
