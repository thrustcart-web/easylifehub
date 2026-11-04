#!/usr/bin/env python3
"""Build AutoBlog-Installer.gs - one file that contains the whole project.

The output is pasted into script.google.com. Running install() creates the
GitHub repo and uploads every embedded file through the GitHub API, which is
the only reliable way to get hidden folders (.github/workflows) onto GitHub
from a browser - drag-and-drop upload silently drops them.

    python3 tools/build_installer.py
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "AutoBlog-Installer.gs"
PAYLOAD_JS = ROOT / "payload.js"

SKIP_DIRS = {".git", "__pycache__", ".cache", "node_modules"}
# state.json is the LIVE progress of a running blog (what was generated and
# published). Overwriting it would re-publish articles or lose the cooldown
# memory, so the installer never touches it.
SKIP_NAMES = {"AutoBlog-Project.zip", "AutoBlog-Installer.gs", "payload.js",
              "preview_server.py", "config-FIXED.json", "build_installer.py",
              "state.json"}
SKIP_PREFIXES = ("content/queue/", "content/categories/")

HEADER = '''/**
 * ============================================================================
 *  AutoBlog - INSTALLER  (ملف واحد فيه المشروع كله)
 * ============================================================================
 *  انت مش محتاج كمبيوتر ولا ZIP ولا ترفع ملفات واحدة واحدة.
 *
 *  1) script.google.com  ->  New project
 *  2) امسح اللي في الملف والصق ده كله (Ctrl+A ثم Ctrl+V) واضغط Save
 *  3) عدّل القيم في CONFIG تحت (التوكن بس هو اللي ناقصك)
 *  4) شغّل الدالة  install()          -> تعمل الريبو وترفع كل الملفات لوحدها
 *  5) شغّل  verifyInstall()           -> تأكد إن كل الملفات وصلت
 *  6) شغّل  setupPublisher()          -> تكتب الـ Script Properties
 *  7) حط SA_JSON و BLOG_ID يدوي في Project Settings -> Script Properties
 *  8) شغّل  testConnection()          -> لازم يطبع OK في كل السطور
 *  9) شغّل  installTrigger()          -> التركيب خلص، انسى الموضوع
 *
 *  ليه الطريقة دي؟
 *  رفع الملفات بالسحب والإفلات على GitHub بيسقّط مجلد .github (مخفي)،
 *  فلو مفيش workflows النظام كله مش هيشتغل. الـ API بيرفع كل حاجة صح.
 * ============================================================================
 */

// ═══════════════════════ عدّل القيم دي ═══════════════════════
var CONFIG = {
  // github.com/settings/tokens -> Generate new token (classic) -> صلاحية repo
  GITHUB_TOKEN:  'PUT_YOUR_GITHUB_PAT_HERE',
  GITHUB_USER:   '__USER__',
  REPO_NAME:     '__REPO__',

  // بيانات المدونة (معبّاة جاهزة)
  BLOG_ID:       '__BLOG_ID__',
  BLOG_URL:      '__BLOG_URL__',
  AFFILIATE_TAG: '__TAG__',
  NOTIFY_EMAIL:  '__EMAIL__',
  TZ:            'Africa/Cairo',
  POST_HOURS:    '9,13,18',
  MAX_PER_DAY:   '3'
};
// ═════════════════════════════════════════════════════════════
'''

INSTALLER_BODY = '''
var RAW_BASE = 'https://raw.githubusercontent.com/' + CONFIG.GITHUB_USER + '/' + CONFIG.REPO_NAME + '/main';

// ------------------------------------------------------------------ helpers --
function gh_(path, method, body) {
  var res = UrlFetchApp.fetch('https://api.github.com' + path, {
    method: method,
    headers: {
      'Authorization': 'token ' + CONFIG.GITHUB_TOKEN,
      'Accept': 'application/vnd.github+json',
      'User-Agent': 'autoblog-installer',
      'Content-Type': 'application/json'
    },
    payload: JSON.stringify(body),
    muteHttpExceptions: true
  });
  var code = res.getResponseCode();
  var text = res.getContentText();
  if (code >= 400) throw new Error('GitHub ' + method + ' ' + path + ' -> ' + code + ': ' + text.slice(0, 300));
  return text ? JSON.parse(text) : {};
}

function decode_(b64) {
  return Utilities.newBlob(Utilities.base64Decode(b64)).getDataAsString('UTF-8');
}

/** Split the payload into chunks - Apps Script chokes on one giant JSON.parse. */
function payloadFiles_() {
  var files = {};
  PAYLOAD_PARTS.forEach(function (part) {
    var chunk = JSON.parse(part);
    Object.keys(chunk).forEach(function (k) { files[k] = chunk[k]; });
  });
  return files;
}

// ------------------------------------------------------------------ install --
/** Creates the repo (if needed) and uploads every embedded file. Idempotent. */
function install() {
  if (CONFIG.GITHUB_TOKEN.indexOf('PUT_YOUR') === 0) {
    Logger.log('STOP: حط الـ Personal Access Token في CONFIG.GITHUB_TOKEN الأول.');
    Logger.log('      github.com/settings/tokens -> Generate new token (classic) -> صلاحية repo');
    return;
  }
  var files = payloadFiles_();
  var names = Object.keys(files);
  Logger.log('Project files embedded: ' + names.length);

  try {
    gh_('/user/repos', 'post', { name: CONFIG.REPO_NAME, private: false, auto_init: false });
    Logger.log('Repo created.');
  } catch (e) {
    Logger.log('Repo already exists (or could not be created) - continuing.');
  }
  Utilities.sleep(4000);

  var ok = 0, failed = [];
  for (var i = 0; i < names.length; i++) {
    var path = names[i];
    var content = decode_(files[path]);
    var encoded = Utilities.base64Encode(Utilities.newBlob(content, 'UTF-8').getBytes());
    var api = '/repos/' + CONFIG.GITHUB_USER + '/' + CONFIG.REPO_NAME + '/contents/' + path;
    var sha = null;
    try { sha = (gh_(api, 'get', {}) || {}).sha || null; } catch (e) { sha = null; }
    var body = {
      message: 'AutoBlog install: ' + path,
      content: encoded,
      branch: 'main'
    };
    if (sha) body.sha = sha;
    try {
      gh_(api, 'put', body);
      ok++;
    } catch (e) {
      failed.push(path);
      Logger.log('FAILED ' + path + ': ' + String(e).slice(0, 160));
    }
    if (i % 6 === 0) Utilities.sleep(1500);   // stay well inside the rate limit
  }

  Logger.log('');
  Logger.log('Uploaded: ' + ok + ' / ' + names.length + ' files');
  if (failed.length) Logger.log('FAILED (' + failed.length + '): ' + failed.join(', '));
  Logger.log('Repo: https://github.com/' + CONFIG.GITHUB_USER + '/' + CONFIG.REPO_NAME);
  Logger.log('');
  Logger.log('Next: run verifyInstall(), then setupPublisher().');
}

/** Verify every file is reachable on raw.githubusercontent.com. */
function verifyInstall() {
  var files = payloadFiles_();
  var names = Object.keys(files);
  var missing = [];
  for (var i = 0; i < names.length; i++) {
    try {
      var r = UrlFetchApp.fetch(RAW_BASE + '/' + names[i], { muteHttpExceptions: true });
      if (r.getResponseCode() !== 200) missing.push(names[i]);
    } catch (e) { missing.push(names[i]); }
    if (i % 10 === 0) Utilities.sleep(600);
  }
  Logger.log(missing.length === 0
    ? 'OK - all ' + names.length + ' files verified on GitHub.'
    : 'MISSING (' + missing.length + '): ' + missing.join(', '));
}

// ------------------------------------------------- publisher configuration --
/** Writes every Script Property the publisher needs, with your values pre-filled. */
function setupPublisher() {
  var P = PropertiesService.getScriptProperties();
  P.setProperty('RAW_BASE', RAW_BASE);
  P.setProperty('QUEUE_DIR', 'content/queue');
  P.setProperty('BLOG_ID', CONFIG.BLOG_ID);
  P.setProperty('BLOG_URL', CONFIG.BLOG_URL);
  P.setProperty('SA_JSON', P.getProperty('SA_JSON') || '');
  P.setProperty('POST_HOURS', CONFIG.POST_HOURS);
  P.setProperty('MAX_PER_DAY', CONFIG.MAX_PER_DAY);
  P.setProperty('JITTER_MAX', '1200');
  P.setProperty('TZ', CONFIG.TZ);
  P.setProperty('MIN_GAP_HOURS', '2');
  P.setProperty('NOTIFY_EMAIL', CONFIG.NOTIFY_EMAIL);
  P.setProperty('CATEGORY_HUB', 'content/categories/index.html');
  P.setProperty('CATEGORY_PAGE', 'Browse All Categories');
  P.setProperty('RELATED_LIMIT', '4');
  P.setProperty('RELATED_ENABLED', 'true');

  Logger.log('Script Properties written:');
  ['RAW_BASE', 'BLOG_ID', 'BLOG_URL', 'POST_HOURS', 'MAX_PER_DAY', 'TZ', 'NOTIFY_EMAIL',
   'CATEGORY_HUB', 'CATEGORY_PAGE'].forEach(function (k) {
    Logger.log('  ' + k + ' = ' + (P.getProperty(k) || '(empty)'));
  });
  Logger.log('');
  if (!P.getProperty('SA_JSON')) {
    Logger.log('LASA BAQY: SA_JSON فاضي.');
    Logger.log('Project Settings -> Script Properties -> SA_JSON');
    Logger.log('-> الصق فيه محتوى ملف الـ service account JSON كامل (بما فيه private_key)');
    Logger.log('بعدين شغّل testConnection()');
  } else {
    Logger.log('SA_JSON موجود. شغّل testConnection() وبعدين installTrigger().');
  }
}
'''


def collect() -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_NAMES or path.suffix == ".pyc":
            continue
        if any(rel.startswith(pfx) for pfx in SKIP_PREFIXES):
            if path.name != ".gitkeep":
                continue
        data = path.read_bytes()
        files[rel] = base64.b64encode(data).decode("ascii")
    return files


def main() -> int:
    import re

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    blog = cfg.get("blog", {})
    aff = cfg.get("affiliate", {})
    gh = cfg.get("github", {})
    notify = cfg.get("notify", {})

    m = re.search(r"githubusercontent\.com/([^/]+)/([^/]+)/", gh.get("repo_raw_base", ""))
    user, repo = (m.group(1), m.group(2)) if m else ("PUT_YOUR_GITHUB_USERNAME", "easylifehub")

    header = (HEADER
              .replace("__USER__", user)
              .replace("__REPO__", repo)
              .replace("__BLOG_ID__", blog.get("id", ""))
              .replace("__BLOG_URL__", blog.get("url", ""))
              .replace("__TAG__", aff.get("tag", ""))
              .replace("__EMAIL__", notify.get("email", "")))

    files = collect()
    # split into chunks so no single JSON.parse has to swallow ~1 MB
    chunk: dict[str, str] = {}
    parts: list[str] = []
    size = 0
    for name in sorted(files):
        blob = files[name]
        if size + len(blob) > 600_000 and chunk:
            parts.append(json.dumps(chunk, separators=(",", ":")))
            chunk, size = {}, 0
        chunk[name] = blob
        size += len(blob) + len(name)
    if chunk:
        parts.append(json.dumps(chunk, separators=(",", ":")))

    payload_js = "var PAYLOAD_PARTS = [\n"
    payload_js += ",\n".join(json.dumps(p) for p in parts)
    payload_js += "\n];\n"

    publisher = (ROOT / "appsscript" / "Code.gs").read_text(encoding="utf-8")
    # strip the publisher's own header comment block; the installer has one
    publisher = publisher.split("*/", 1)[-1].lstrip("\n")

    out = (header + "\n" + payload_js + "\n" + INSTALLER_BODY + "\n\n"
           + "// ═══════════════ PUBLISHER  (appsscript/Code.gs) ═══════════════\n\n"
           + publisher)
    OUT.write_text(out, encoding="utf-8")
    PAYLOAD_JS.write_text(payload_js, encoding="utf-8")

    print(f"files embedded : {len(files)}")
    print(f"payload parts  : {len(parts)}")
    print(f"output         : {OUT.name}  ({OUT.stat().st_size/1024:.0f} KB)")
    print(f"github target  : {user}/{repo}")
    print(f"blog           : {blog.get('url')} (id {blog.get('id')})")
    print(f"tag            : {aff.get('tag')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
