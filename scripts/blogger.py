"""Blogger API v3 publisher (service-account JWT, no user OAuth, no email).

Why a service account instead of OAuth:
  * OAuth refresh tokens expire when the Google Cloud project is in "Testing"
    mode (7 days) and can be revoked after ~6 months of non-use. That breaks
    "set up and forget".
  * A service-account key never expires. You add its email address as an
    *Author* on the blog once, and the pipeline can post forever.

Used by the local/CI `--publish blogger` path. The production publisher is
appsscript/Code.gs (same logic, better timing precision) - this module is what
lets you test and backfill from your own machine.

Requires: pip install cryptography   (only for direct publishing; generation and
dry-runs work with the standard library alone)
"""
from __future__ import annotations

import base64
import json
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from util import log

TOKEN_URI = "https://oauth2.googleapis.com/token"
BLOGGER_API = "https://blogger.googleapis.com/v3"
SCOPE = "https://www.googleapis.com/auth/blogger"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _load_sa(sa_json: dict | str) -> dict:
    if isinstance(sa_json, str):
        return json.loads(sa_json)
    return sa_json


def get_access_token(sa: dict) -> str:
    """Sign a JWT with the service-account private key and exchange it."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Direct publishing needs the 'cryptography' package: pip install cryptography\n"
            "(Generation and --dry-run work without it. The Apps Script publisher needs nothing.)"
        ) from exc

    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claim = {
        "iss": sa["client_email"],
        "scope": SCOPE,
        "aud": TOKEN_URI,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claim).encode())}".encode()
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    assertion = signing_input + b"." + _b64url(signature)

    body = urlparse.urlencode(
        {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion.decode()}
    ).encode()
    req = urlrequest.Request(TOKEN_URI, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlrequest.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


def _call(token: str, method: str, path: str, payload: dict | None = None, params: dict | None = None) -> dict:
    url = f"{BLOGGER_API}{path}"
    if params:
        url += "?" + urlparse.urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urlrequest.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "AutoBlog/1.0",
        },
        method=method,
    )
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Blogger API {method} {path} -> HTTP {exc.code}: {detail}") from exc


class Blogger:
    def __init__(self, sa: dict | str, blog_id: str):
        self.sa = _load_sa(sa)
        self.blog_id = str(blog_id)
        self._token: str | None = None
        self._token_ts = 0.0

    @property
    def token(self) -> str:
        if not self._token or time.time() - self._token_ts > 3000:
            self._token = get_access_token(self.sa)
            self._token_ts = time.time()
        return self._token

    # ---------------------------------------------------------------- reads --
    def get_blog(self) -> dict:
        return _call(self.token, "GET", f"/blogs/{self.blog_id}")

    def list_posts(self, max_results: int = 50, status: str = "LIVE") -> list[dict]:
        data = _call(self.token, "GET", f"/blogs/{self.blog_id}/posts", {"maxResults": max_results, "status": status})
        return data.get("items", [])

    # --------------------------------------------------------------- writes --
    def insert_post(self, title: str, html: str, labels: list[str] | None = None,
                    is_draft: bool = False, publish_date: str | None = None) -> dict:
        payload: dict = {"title": title, "content": html, "labels": labels or []}
        params = {"isDraft": "true"} if is_draft else {}
        if publish_date:                      # ISO 8601 -> Blogger schedules it
            payload["published"] = publish_date
        result = _call(self.token, "POST", f"/blogs/{self.blog_id}/posts", payload, params or None)
        log.info("  Blogger post created: id=%s status=%s", result.get("id"), "draft" if is_draft else "live")
        return result

    def publish_draft(self, post_id: str) -> dict:
        return _call(self.token, "POST", f"/blogs/{self.blog_id}/posts/{post_id}/publish")

    def delete_post(self, post_id: str) -> None:
        _call(self.token, "DELETE", f"/blogs/{self.blog_id}/posts/{post_id}")


def verify_setup(sa: dict | str, blog_id: str) -> dict:
    """Connectivity + permission check. Run this once after setup."""
    client = Blogger(sa, blog_id)
    blog = client.get_blog()
    drafts = client.list_posts(status="DRAFT")
    return {
        "ok": True,
        "blog_name": blog.get("name"),
        "blog_url": blog.get("url"),
        "posts_live": len(client.list_posts(status="LIVE")),
        "posts_draft": len(drafts),
        "service_account": client.sa.get("client_email"),
    }
