#!/usr/bin/env python3
"""Local preview of the queue - see exactly what will land on Blogger.

    python3 preview_server.py            # http://localhost:8000

Renders content/queue/*.html inside a Blogger-like page shell. No network
access needed: the post CSS is inline and the hero images are embedded SVG,
so it looks the same here as it will on your blog.
"""
from __future__ import annotations

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
QUEUE = ROOT / "content" / "queue"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#eef1f5;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1c1f23}}
.topbar{{position:sticky;top:0;z-index:20;background:#101418;color:#fff;padding:.55rem 1rem;display:flex;gap:1rem;align-items:center;flex-wrap:wrap;box-shadow:0 2px 10px rgba(0,0,0,.25)}}
.topbar a{{color:#9fd0ff;text-decoration:none;font-size:.85rem}}
.topbar .brand{{font-weight:800;letter-spacing:-.02em;font-size:1rem;margin-right:.5rem}}
.topbar .meta{{color:#98a2b3;font-size:.78rem;margin-left:auto}}
.layout{{display:flex;gap:1.25rem;max-width:1180px;margin:1.25rem auto;padding:0 1rem;align-items:flex-start}}
.side{{flex:0 0 285px;background:#fff;border:1px solid #e2e6ec;border-radius:12px;padding:.9rem 1rem;max-height:calc(100vh - 120px);overflow:auto;position:sticky;top:64px}}
.side h3{{margin:.1rem 0 .7rem;font-size:.74rem;letter-spacing:.09em;text-transform:uppercase;color:#5b6472}}
.side a{{display:block;padding:.42rem .5rem;border-radius:8px;color:#24292f;text-decoration:none;font-size:.86rem;line-height:1.35;border-left:3px solid transparent}}
.side a:hover{{background:#f4f6f9}}
.side a.on{{background:#eef5ff;border-left-color:#146c43;font-weight:700}}
.side .tag{{display:inline-block;font-size:.66rem;background:#f0f2f5;border-radius:999px;padding:.05rem .45rem;color:#5b6472;margin-left:.3rem}}
.main{{flex:1 1 auto;background:#fff;border:1px solid #e2e6ec;border-radius:12px;padding:1.6rem 1.8rem 2.2rem;min-width:0}}
.main h1.pt{{font-size:1.95rem;line-height:1.22;margin:.1rem 0 .5rem;letter-spacing:-.02em}}
.byline{{color:#5b6472;font-size:.82rem;border-bottom:1px solid #eef0f3;padding-bottom:.9rem;margin-bottom:1.3rem}}
.pill{{display:inline-block;background:#f0f7f3;color:#146c43;border-radius:999px;padding:.14rem .6rem;font-size:.72rem;font-weight:700;margin-right:.35rem}}
.empty{{padding:3rem 1rem;text-align:center;color:#5b6472}}
@media(max-width:900px){{.layout{{flex-direction:column}}.side{{position:static;width:100%;flex:auto;max-height:none}}.main{{padding:1.1rem}}}}
</style></head>
<body>
<div class="topbar">
  <span class="brand">AutoBlog Preview</span>
  <a href="/">All posts</a>
  <a href="/?check=1">SEO &amp; link check</a>
  <span class="meta">{meta}</span>
</div>
<div class="layout">
  <nav class="side"><h3>Queue ({count})</h3>{nav}</nav>
  <article class="main">{body}</article>
</div>
</body></html>"""


def load_entries() -> list[dict]:
    out = []
    for path in sorted(QUEUE.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        meta["_html_path"] = str(path.with_suffix(".html"))
        out.append(meta)
    return sorted(out, key=lambda m: (m.get("template", ""), m.get("title", "")))


def nav_html(entries: list[dict], active: str) -> str:
    items = []
    for entry in entries:
        slug = entry["slug"]
        on = ' class="on"' if slug == active else ""
        badge = {"bestof": "list", "comparison": "vs", "review": "rev"}.get(entry.get("template"), "")
        items.append(
            f'<a href="/?post={slug}"{on}>{html.escape(entry["title"][:70])}'
            f'<span class="tag">{badge}</span></a>'
        )
    return "\n".join(items) or "<em>Queue is empty.</em>"


def check_report(entries: list[dict]) -> str:
    import re

    rows = []
    problems = 0
    for entry in entries:
        try:
            body = Path(entry["_html_path"]).read_text(encoding="utf-8")
        except OSError:
            continue
        issues = []
        if "tag=" not in body:
            issues.append("no affiliate tag in links")
        if "disclosure" not in body.lower():
            issues.append("missing disclosure")
        if 'rel="nofollow sponsored noopener"' not in body:
            issues.append("missing rel attributes")
        if "application/ld+json" not in body:
            issues.append("no schema.org")
        words = len(re.findall(r"[\w'-]+", re.sub(r"<[^>]+>", " ", body)))
        if words < 900:
            issues.append(f"thin ({words}w)")
        links = len(re.findall(r'href="https://www\.amazon\.[^"]+"', body))
        problems += len(issues)
        rows.append(
            f"<tr><td>{html.escape(entry['title'][:60])}</td><td>{words}</td><td>{links}</td>"
            f"<td>{entry.get('source','')}</td><td>{'<br>'.join(issues) or '<span style=color:#146c43>OK</span>'}</td></tr>"
        )
    summary = f"{len(rows)} posts checked &middot; {problems} issues"
    table = (
        '<table style="width:100%;border-collapse:collapse;font-size:.85rem">'
        "<thead><tr style='text-align:left;background:#f6f8fa'>"
        "<th style='padding:.5rem'>Title</th><th>Words</th><th>Links</th><th>Source</th><th>Issues</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return f"<h1 class='pt'>Queue health check</h1><p class='byline'>{summary}</p>{table}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console readable
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        entries = load_entries()

        if "check" in query:
            body, active = check_report(entries), ""
        elif "post" in query:
            slug = query["post"][0]
            entry = next((e for e in entries if e["slug"] == slug), None)
            if not entry:
                body, active = "<div class='empty'>Post not found.</div>", ""
            else:
                try:
                    inner = Path(entry["_html_path"]).read_text(encoding="utf-8")
                except OSError:
                    inner = "<div class='empty'>HTML file missing.</div>"
                pills = "".join(
                    f'<span class="pill">{html.escape(str(t))}</span>'
                    for t in [entry.get("template"), entry.get("niche"), f"{entry.get('words', 0)} words", entry.get("source")]
                )
                meta_line = html.escape(entry.get("meta_description", ""))
                body = f"<h1 class='pt'>{html.escape(entry['title'])}</h1><div class='byline'>{pills}<br><br><em>{meta_line}</em></div><div class='post-body'>{inner}</div>"
                active = slug
        else:
            if not entries:
                body = "<div class='empty'><h2>The queue is empty</h2><p>Run <code>python3 main.py --count 5 --mode template</code> to generate articles.</p></div>"
            else:
                total_words = sum(e.get("words", 0) for e in entries)
                cards = "".join(
                    f'<a href="/?post={e["slug"]}" style="display:block;padding:.85rem;border:1px solid #e6e8ec;border-radius:10px;margin-bottom:.6rem;text-decoration:none;color:inherit">'
                    f'<strong style="font-size:.98rem;line-height:1.35;display:block">{html.escape(e["title"])}</strong>'
                    f'<span style="color:#5b6472;font-size:.78rem">{e.get("template")} &middot; {e.get("niche")} &middot; {e.get("words",0)} words &middot; {len(e.get("items",[]))} product links</span></a>'
                    for e in entries
                )
                body = (
                    f"<h1 class='pt'>{len(entries)} articles ready to publish</h1>"
                    f"<p class='byline'>{total_words:,} words total &middot; roughly {total_words // 250} minutes of reading &middot; "
                    f"{len(entries) // 3 or 1} days of publishing at 3/day</p>{cards}"
                )
            active = ""

        page = SHELL.format(
            title="AutoBlog preview",
            meta=f"{len(entries)} queued",
            count=len(entries),
            nav=nav_html(entries, active),
            body=body,
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"AutoBlog preview -> http://localhost:{PORT}   ({len(load_entries())} posts queued)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
