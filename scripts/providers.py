"""Free AI providers with automatic fallback.

Design goal: the system must NEVER stop publishing because one free tier ran
out of quota. Providers are tried in order; every one of them is free and
needs no credit card (as of late 2026):

  groq       - 1,000 requests/day, 30 RPM, 200k tokens/day  (primary)
  mistral    - free-mode credits, per-project caps
  openrouter - 50 requests/day on ":free" models
  gemini     - free tier, caps shown per project in AI Studio (heavily cut)

If *all* providers fail, scripts/template_engine.py still produces a complete,
SEO-structured article offline. That is the "set up and forget" guarantee.
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib import error as urlerror
from urllib import request as urlrequest

from util import log

USER_AGENT = "Mozilla/5.0 (compatible; AutoBlog/1.0; +https://github.com/)"


class AIError(RuntimeError):
    pass


# --------------------------------------------------------------- plumbing ---
def _post_json(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    last = None
    for attempt in range(4):
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urlerror.HTTPError as exc:  # noqa: PERF203
            body = exc.read().decode("utf-8", "replace")[:400]
            last = f"HTTP {exc.code}: {body}"
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                wait = (2 ** attempt) * 8 + 2
                log.warning("  %s -> %s, retrying in %ss", url.split("/")[2], exc.code, wait)
                time.sleep(wait)
                continue
            raise AIError(last) from exc
        except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = str(exc)
            if attempt < 3:
                time.sleep((2 ** attempt) * 5)
                continue
            raise AIError(last) from exc
    raise AIError(last or "unknown error")


def extract_json(text: str) -> dict:
    """LLMs wrap JSON in prose/fences. Pull the first balanced object out."""
    if not text:
        raise AIError("empty response")
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)  # trailing commas
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise AIError("no parseable JSON object in model response")


# -------------------------------------------------------------- providers ---
def _openai_style(base_url: str, api_key: str, model: str, system: str, user: str,
                   temperature: float, max_tokens: int) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT}
    try:
        data = _post_json(f"{base_url}/chat/completions", payload, headers)
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        # some free endpoints reject response_format -> retry without it
        payload.pop("response_format", None)
        data = _post_json(f"{base_url}/chat/completions", payload, headers)
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc2:  # noqa: BLE001
            raise AIError(f"unexpected response shape: {exc2}") from exc


def _groq(cfg: dict, system: str, user: str, temp: float, max_tokens: int) -> str:
    key = os.environ.get(cfg["env_key"], "")
    if not key:
        raise AIError("GROQ_API_KEY not set")
    models = [cfg["model"], *cfg.get("fallback_models", [])]
    last = ""
    for model in models:
        try:
            return _openai_style(cfg["base_url"], key, model, system, user, temp, max_tokens)
        except AIError as exc:
            last = f"{model}: {exc}"
            log.warning("  groq/%s failed (%s)", model, str(exc)[:120])
    raise AIError(last)


def _mistral(cfg: dict, system: str, user: str, temp: float, max_tokens: int) -> str:
    key = os.environ.get(cfg["env_key"], "")
    if not key:
        raise AIError("MISTRAL_API_KEY not set")
    return _openai_style(cfg["base_url"], key, cfg["model"], system, user, temp, max_tokens)


def _openrouter(cfg: dict, system: str, user: str, temp: float, max_tokens: int) -> str:
    key = os.environ.get(cfg["env_key"], "")
    if not key:
        raise AIError("OPENROUTER_API_KEY not set")
    headers_extra = {
        "HTTP-Referer": os.environ.get("SITE_URL", "https://github.com/"),
        "X-Title": "AutoBlog Affiliate",
    }
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temp,
        "max_tokens": max_tokens,
    }
    data = _post_json(
        f"{cfg['base_url']}/chat/completions",
        payload,
        {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT, **headers_extra},
    )
    return data["choices"][0]["message"]["content"]


def _gemini(cfg: dict, system: str, user: str, temp: float, max_tokens: int) -> str:
    key = os.environ.get(cfg["env_key"], "")
    if not key:
        raise AIError("GEMINI_API_KEY not set")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temp,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    url = f"{cfg['base_url']}/models/{cfg['model']}:generateContent?key={key}"
    data = _post_json(url, payload, {"User-Agent": USER_AGENT})
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise AIError(f"gemini blocked/empty: {json.dumps(data)[:200]}") from exc


PROVIDERS = {
    "groq": _groq,
    "mistral": _mistral,
    "openrouter": _openrouter,
    "gemini": _gemini,
}


# ----------------------------------------------------------------- public ---
def generate_json(ai_cfg: dict, system: str, user: str, timeout_note: str = "") -> tuple[dict, str]:
    """Try primary then fallbacks. Returns (parsed_json, provider_used)."""
    order = [ai_cfg.get("primary", "groq")] + [p for p in ai_cfg.get("fallbacks", []) if p != ai_cfg.get("primary")]
    temp = float(ai_cfg.get("temperature", 0.85))
    max_tokens = int(ai_cfg.get("max_output_tokens", 6000))
    errors = []
    for name in order:
        fn = PROVIDERS.get(name)
        if not fn:
            errors.append(f"{name}: unknown provider")
            continue
        cfg = ai_cfg.get("providers", {}).get(name)
        if not cfg:
            errors.append(f"{name}: not configured")
            continue
        try:
            log.info("  AI provider: %s %s", name, timeout_note)
            raw = fn(cfg, system, user, temp, max_tokens)
            parsed = extract_json(raw)
            log.info("  AI provider %s OK (%d chars)", name, len(raw))
            return parsed, name
        except AIError as exc:
            errors.append(f"{name}: {exc}")
            log.warning("  provider %s failed: %s", name, str(exc)[:200])
        except Exception as exc:  # noqa: BLE001 - never die on a free API quirk
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            log.warning("  provider %s crashed: %s", name, str(exc)[:200])
    raise AIError("all providers failed -> " + " | ".join(errors))


def available_providers(ai_cfg: dict) -> list[str]:
    out = []
    for name in [ai_cfg.get("primary", "groq")] + ai_cfg.get("fallbacks", []):
        cfg = ai_cfg.get("providers", {}).get(name)
        if cfg and os.environ.get(cfg.get("env_key", ""), ""):
            out.append(name)
    return out
