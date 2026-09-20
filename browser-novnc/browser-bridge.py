#!/usr/bin/env python3
"""HTTP bridge API for the browser-novnc container.

Exposes endpoints that the SocialAuto backend can call to:
  - Start a browser session for any social platform (opens login page)
  - Check session status (waiting / logged_in / cookies extracted)
  - Retrieve extracted cookies
  - Get the noVNC URL for embedding in the frontend iframe

The bridge runs on port 9223 inside the container.
"""

import asyncio
import contextvars
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

COOKIE_DIR = Path("/app/cookies")
COOKIE_DIR.mkdir(parents=True, exist_ok=True)


def _safe_path_component(value: str | None, max_length: int = 128) -> str:
    """Reduce an untrusted string to a single safe path component."""
    if not value:
        return "unnamed"
    value = re.sub(r"[\x00-\x1f\\/]+", "_", str(value))
    value = re.sub(r"[^\w.\-]", "_", value)
    value = value.strip("._")
    if value in ("", ".", ".."):
        value = "unnamed"
    return value[:max_length]


def _cookie_file(*parts: str) -> Path:
    """Resolve a path under COOKIE_DIR; raise if it escapes the cookie root."""
    safe_parts = tuple(_safe_path_component(p) for p in parts)
    base = COOKIE_DIR.resolve()
    candidate = Path(base, *safe_parts).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Resolved path {candidate!r} escapes root {base!r}") from exc
    return candidate


def _is_instagram_host(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "instagram.com" or host.endswith(".instagram.com")
    except Exception:
        return False

SITES = {
    "instagram": {
        "url": "https://www.instagram.com/accounts/login/",
        "success_patterns": ["instagram.com/?", "instagram.com/accounts/one_tap_app_login"],
        "cookies": ["sessionid", "csrftoken", "ds_user_id", "ig_did", "mid", "rur"],
    },
    "facebook": {
        "url": "https://www.facebook.com/login",
        "success_patterns": ["facebook.com/?sk=", "facebook.com/home", "facebook.com/?ref="],
        "cookies": ["c_user", "xs", "datr", "fr", "sb", "wd"],
    },
    "linkedin": {
        "url": "https://www.linkedin.com/login",
        "success_patterns": ["linkedin.com/feed", "linkedin.com/in/"],
        "cookies": ["li_at", "JSESSIONID", "liap", "bcookie", "bscookie", "lang"],
    },
    "tiktok": {
        "url": "https://www.tiktok.com/login",
        "success_patterns": ["tiktok.com/foryou", "tiktok.com/following"],
        "cookies": ["sessionid", "sid_tt", "uid_tt", "ttwid", "msToken", "passport_csrf_token"],
    },
    "twitter": {
        "url": "https://x.com/i/flow/login",
        "success_patterns": ["x.com/home", "x.com/compose"],
        "cookies": ["auth_token", "ct0", "twid", "kdt", "guest_id"],
    },
    "threads": {
        "url": "https://www.threads.com/login",
        "success_patterns": ["threads.net/@", "threads.net/home", "threads.com/@", "threads.com/home"],
        "cookies": ["sessionid", "csrftoken", "ds_user_id", "ig_did"],
    },
    "reddit": {
        "url": "https://www.reddit.com/login",
        "success_patterns": ["reddit.com/?", "reddit.com/home", "reddit.com/user/"],
        "cookies": ["reddit_session", "token", "loid", "csv"],
    },
    "youtube": {
        "url": "https://accounts.google.com/v3/signin/identifier?continue=https://www.youtube.com",
        "success_patterns": ["youtube.com/feed", "youtube.com/channel"],
        "cookies": ["SAPISID", "SSID", "HSID", "APISID", "SID", "LOGIN_INFO", "__Secure-3PSID"],
    },
    "pinterest": {
        "url": "https://www.pinterest.com/login/",
        "success_patterns": ["pinterest.com/?", "pinterest.com/home", "pinterest.com/ideas"],
        "cookies": ["_pinterest_sess", "csrftoken", "pinterest_ct", "auth_expires"],
    },
    "tumblr": {
        "url": "https://www.tumblr.com/login",
        "success_patterns": ["tumblr.com/dashboard", "tumblr.com/feed"],
        "cookies": ["pfs", "pfp", "user_props", "logging"],
    },
    "medium": {
        "url": "https://medium.com/m/signin",
        "success_patterns": ["medium.com/me", "medium.com/?source"],
        "cookies": ["sid", "uid", "sess", "__cf_bm"],
    },
    "discord": {
        "url": "https://discord.com/login",
        "success_patterns": ["discord.com/channels", "discord.com/app"],
        "cookies": ["token", "discord_showcase", "__cfruid"],
    },
    "telegram": {
        "url": "https://web.telegram.org/a/",
        "success_patterns": ["web.telegram.org/a/#"],
        "cookies": ["stel_token", "tg_user", "sph_phone", "sph_hash"],
    },
    "whatsapp": {
        "url": "https://web.whatsapp.com/",
        "success_patterns": ["web.whatsapp.com/?", "web.whatsapp.com/#"],
        "cookies": ["wa_web_prefs", "wa_csrf_token"],
    },
}

# Global state for the active browser session
_state: dict[str, Any] = {
    "platform": None,
    "status": "idle",  # idle, waiting, logged_in, extracting, done, error
    "message": "",
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "cookies": {},
    "lock": asyncio.Lock(),
    # Cross-platform hijack guard: tagged live-page interactions extend
    # busy_until under busy_owner; foreign-platform and untagged callers
    # get 409 while the hold is active. Untagged calls never set the hold,
    # so they can't lock themselves out — they're just unprotected.
    "busy_until": 0.0,
    "busy_owner": None,
    # When the current session entered "waiting" (login window opened).
    # A waiting session older than WAITING_TIMEOUT is treated as abandoned
    # and can be preempted by another platform's /session/start — otherwise
    # a poller that opens a login page nobody uses would block the browser
    # for the full 10-minute login-detection loop.
    "waiting_since": 0.0,
    # True when the waiting session was started for a human noVNC login —
    # grants INTERACTIVE_WAITING_TIMEOUT instead of WAITING_TIMEOUT.
    "interactive": False,
}

# Seconds a platform keeps exclusive use of the browser after its last
# interaction. Long enough to cover a multi-step compose (navigate, type,
# attach, post) including slow media uploads; short enough that a crashed
# caller frees the browser for pollers within minutes.
BUSY_HOLD_SECONDS = 180.0

# Seconds a "waiting" session (login window open, nobody authenticated yet)
# may block other platforms before it is treated as abandoned and can be
# preempted. Must be long enough for a real human/service login via noVNC
# (~1-2 min) plus margin, short enough that a stale poller session can't
# starve publishers for the full 10-minute detection loop.
WAITING_TIMEOUT = 300.0

# Interactive sessions (a human is logging in via noVNC) get a much longer
# abandoned-login window — typing creds, solving a captcha, or fetching a
# 2FA code legitimately takes longer than a poller's automated attempt.
INTERACTIVE_WAITING_TIMEOUT = 1800.0

# Callers identify themselves with the X-Platform header; while the
# busy-hold is active only requests tagged with the owning platform may
# touch the page. Middleware copies the header into this contextvar so
# _ensure_live_page can enforce ownership without endpoint changes.
_req_platform: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "req_platform", default=None
)

app = FastAPI(title="Browser Bridge", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _platform_tag_middleware(request, call_next):
    token = _req_platform.set(request.headers.get("x-platform"))
    try:
        return await call_next(request)
    finally:
        _req_platform.reset(token)


async def _ensure_live_page():
    """Return a live Playwright page, recovering from a closed tab if possible.

    Persistent-context Chromium sometimes drops the active page while the
    context stays alive (daemon keepalive races, tab crashes). Callers used
    to treat a non-None ``_state['page']`` as valid and then hit
    ``Target page, context or browser has been closed``.
    """
    # #region agent log
    def _dbg(message: str, data: dict) -> None:
        try:
            import json as _json
            import time as _t
            import urllib.request as _urlreq

            payload = _json.dumps(
                {
                    "sessionId": "ce3429",
                    "runId": "post-fix",
                    "hypothesisId": "H2",
                    "location": "browser-bridge._ensure_live_page",
                    "message": message,
                    "data": data,
                    "timestamp": int(_t.time() * 1000),
                }
            ).encode()
            req = _urlreq.Request(
                "http://host.docker.internal:7498/ingest/539d7b50-953d-4771-ac13-21f8bcf3a397",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Debug-Session-Id": "ce3429",
                },
                method="POST",
            )
            _urlreq.urlopen(req, timeout=1).read()
        except Exception:
            pass

    _dbg(
        "ensure_live_page entry",
        {
            "has_page": _state.get("page") is not None,
            "has_context": _state.get("context") is not None,
            "status": _state.get("status"),
        },
    )
    # #endregion

    # Busy-hold enforcement: while a platform is actively using the
    # browser, only requests tagged with that platform (X-Platform header)
    # may touch the page. Foreign-platform and untagged calls get 409 —
    # this stops pollers steering the page mid-login/mid-compose.
    req_plat = _req_platform.get()
    busy_owner = _state.get("busy_owner")
    if time.time() < _state.get("busy_until", 0.0) and req_plat != busy_owner:
        raise HTTPException(
            409, f"Browser busy with {busy_owner or 'another'} session — try again shortly"
        )

    def _touch() -> None:
        if req_plat:
            _state["busy_until"] = time.time() + BUSY_HOLD_SECONDS
            _state["busy_owner"] = req_plat

    page = _state.get("page")
    if page is not None:
        try:
            if not page.is_closed():
                _touch()
                return page
        except Exception:
            pass

    context = _state.get("context")
    if context is None:
        raise HTTPException(400, "No active browser session")

    try:
        for candidate in list(context.pages):
            try:
                closed = candidate.is_closed()
            except Exception:
                continue
            if closed:
                continue
            _state["page"] = candidate
            _touch()
            # #region agent log
            _dbg("recovered existing open page from context", {})
            # #endregion
            return candidate

        page = await context.new_page()
        _state["page"] = page
        _touch()
        # #region agent log
        _dbg("opened new page on existing context", {})
        # #endregion
        return page
    except HTTPException:
        raise
    except Exception as exc:
        _state["page"] = None
        _state["context"] = None
        _state["browser"] = None
        _state["status"] = "idle"
        _state["busy_until"] = 0.0
        _state["busy_owner"] = None
        _state["message"] = f"Browser session died: {exc}"
        # #region agent log
        _dbg("context dead; cleared state", {"error": str(exc)[:200]})
        # #endregion
        raise HTTPException(
            400,
            "Browser session closed — restart via /session/start",
        ) from exc



class StartRequest(BaseModel):
    platform: str
    force: bool = False
    # Human-driven login via noVNC — gets a longer waiting window before the
    # session is treated as abandoned and becomes preemptible by pollers.
    interactive: bool = False


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "novnc_url": "/novnc/vnc.html?autoconnect=1&resize=scale",
        "supported_platforms": list(SITES.keys()),
    }


@app.get("/novnc-url")
async def novnc_url():
    """Return the noVNC web URL for embedding in an iframe.

    Returns a same-origin relative path (/novnc/vnc.html) that is proxied
    by the social-frontend custom server (novnc-server.cjs) to eliminate
    Mixed Content warnings when embedded on https://social.cloudless.gr.
    """
    port = os.environ.get("NOVNC_PORT", "6080")
    return {"url": "/novnc/vnc.html?autoconnect=1&resize=scale", "vnc_port": port}


@app.get("/platforms")
async def list_platforms():
    """List all supported platforms and their target cookies."""
    return {
        platform: {"url": site["url"], "cookies": site["cookies"]}
        for platform, site in SITES.items()
    }


@app.post("/session/start")
async def start_session(req: StartRequest):
    """Start a browser session for a platform — opens the login page.

    Refuses to tear down a browser that is busy with a different platform
    (any live-page interaction extends ``_state['busy_until']``). Callers
    that hit 409 should retry later — this is what stops messenger/social
    pollers from killing an in-flight login or compose. ``force=true``
    overrides the hold for manual recovery.
    """
    async with _state["lock"]:
        platform = req.platform.lower()
        if platform not in SITES:
            raise HTTPException(400, f"Unknown platform: {platform}. Available: {list(SITES.keys())}")

        if _state["status"] in ("waiting", "extracting") and not req.force:
            wait_timeout = (
                INTERACTIVE_WAITING_TIMEOUT if _state.get("interactive") else WAITING_TIMEOUT
            )
            stale_waiting = (
                _state["status"] == "waiting"
                and time.time() - _state.get("waiting_since", 0.0) > wait_timeout
            )
            # Same-platform re-entry during an active login is fine (reuse
            # below); foreign platforms are blocked unless the waiting
            # session is stale — i.e. the login window was abandoned.
            if platform == _state["platform"]:
                return {
                    "platform": _state["platform"],
                    "status": _state["status"],
                    "message": _state["message"],
                    "cookies_found": list(_state["cookies"].keys()),
                    "reused": True,
                }
            if not stale_waiting:
                raise HTTPException(
                    409, f"Session already active for {_state['platform']}"
                )

        busy = (
            _state["browser"] is not None
            and time.time() < _state.get("busy_until", 0.0)
        )
        busy_owner = _state.get("busy_owner")
        if busy and not req.force:
            if platform != busy_owner and platform != _state["platform"]:
                raise HTTPException(
                    409,
                    f"Browser busy with {busy_owner or _state['platform']} session — try again shortly",
                )
            # Caller already owns the session or the busy-hold — reuse it
            # instead of tearing down the context mid-operation.
            return {
                "platform": _state["platform"],
                "status": _state["status"],
                "message": _state["message"],
                "cookies_found": list(_state["cookies"].keys()),
                "reused": True,
            }

        # Cancel any previous session's background task — otherwise its
        # 10-minute login-detection loop keeps a Chromium alive on the
        # shared profile and the new launch dies with "Opening in
        # existing browser session".
        old_task = _state.get("task")
        if old_task and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except (asyncio.CancelledError, Exception):
                pass
            _state["task"] = None

        # Close any existing browser
        if _state["context"]:
            try:
                await _state["context"].close()
            except Exception:
                pass
        elif _state["browser"]:
            try:
                await _state["browser"].close()
            except Exception:
                pass
        _state["browser"] = None
        _state["context"] = None
        _state["page"] = None

        site = SITES[platform]
        _state["platform"] = platform
        _state["status"] = "waiting"
        _state["waiting_since"] = time.time()
        _state["interactive"] = bool(req.interactive)
        # A tagged caller starting its own session takes an immediate
        # busy-hold — without it the window between this start and the
        # caller's first page interaction is unprotected, letting a
        # poller's /session/start tear down a brand-new session.
        caller = _req_platform.get()
        if caller and caller == platform:
            _state["busy_until"] = time.time() + BUSY_HOLD_SECONDS
            _state["busy_owner"] = caller
        else:
            _state["busy_until"] = 0.0
            _state["busy_owner"] = None
        _state["message"] = f"Opening {site['url']} — log in via the noVNC viewer"
        _state["cookies"] = {}

        # Start browser in background
        _state["task"] = asyncio.create_task(_run_browser(platform))

    # Wait for the page to come live before returning — callers fire
    # evaluate/navigate immediately after start, and a 200 with no page
    # yet would 400 their very next call (observed in publish fallback).
    for _ in range(40):
        await asyncio.sleep(0.5)
        if _state["page"] is not None or _state["status"] == "error":
            break

    return {
        "platform": platform,
        "status": "waiting" if _state["page"] is not None else _state["status"],
        "message": _state["message"],
        "novnc_url": "/novnc/vnc.html?autoconnect=1&resize=scale",
    }


@app.get("/session/status")
async def session_status():
    """Check the current session status."""
    return {
        "platform": _state["platform"],
        "status": _state["status"],
        "message": _state["message"],
        "cookies_found": list(_state["cookies"].keys()),
    }


@app.get("/session/cookies")
async def session_cookies():
    """Retrieve extracted cookies (only after status=done)."""
    if _state["status"] != "done":
        raise HTTPException(400, f"Session not done (current: {_state['status']})")
    return {
        "platform": _state["platform"],
        "cookies": _state["cookies"],
        "all_cookies_file": f"/app/cookies/{_state['platform']}_all_cookies.json",
    }


@app.post("/session/stop")
async def stop_session():
    """Stop the current browser session."""
    async with _state["lock"]:
        if _state["context"]:
            try:
                await _state["context"].close()
            except Exception:
                pass
            _state["context"] = None
            _state["browser"] = None
        _state["status"] = "idle"
        _state["platform"] = None
        _state["busy_until"] = 0.0
        _state["busy_owner"] = None
        _state["message"] = "Session stopped"
        return {"status": "stopped"}


# ── Daemon mode: keep session warm for fast sends ──────────────────────
_keepalive_task: asyncio.Task | None = None
_warm_urls: dict[str, str] = {
    "facebook": "https://www.facebook.com/messages/",
    "messenger": "https://www.facebook.com/messages/",
    "instagram": "https://www.instagram.com/direct/inbox/",
    "linkedin": "https://www.linkedin.com/feed/",
}


@app.post("/session/warm")
async def warm_session(platform: str = "facebook"):
    """Pre-load the platform SPA so subsequent sends are fast (~2s vs ~15s cold).

    Navigates to the platform's main page and keeps the session alive with
    periodic background navigation. Call this once after login to warm the
    session for daemon-mode sends.
    """
    global _keepalive_task
    if not _state["context"] or not _state["page"]:
        raise HTTPException(400, "No active browser session — start one first")
    target = _warm_urls.get(platform, _warm_urls.get("facebook", "https://www.facebook.com/messages/"))
    try:
        await _state["page"].goto(target, wait_until="domcontentloaded", timeout=30000)
        _state["message"] = f"Session warmed for {platform}"
    except Exception as exc:
        _state["message"] = f"Warm failed: {exc}"
    # Start keepalive background task (cancels any existing one)
    if _keepalive_task and not _keepalive_task.done():
        _keepalive_task.cancel()
    _keepalive_task = asyncio.create_task(_keepalive_loop(platform))
    return {"status": "warmed", "platform": platform, "url": target}


async def _keepalive_loop(platform: str):
    """Background task that keeps the session alive by navigating every 5 min."""
    target = _warm_urls.get(platform, "https://www.facebook.com/messages/")
    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes
            if _state["page"]:
                await _state["page"].goto(target, wait_until="domcontentloaded", timeout=30000)
        except asyncio.CancelledError:
            break
        except Exception:
            pass  # Non-fatal — keepalive will retry next cycle


@app.get("/session/daemon-status")
async def daemon_status():
    """Check if daemon mode (keepalive) is active."""
    return {
        "daemon_active": _keepalive_task is not None and not _keepalive_task.done(),
        "platform": _state.get("platform"),
        "status": _state.get("status"),
        "message": _state.get("message"),
    }


@app.post("/session/extract")
async def extract_cookies_now():
    """Manually extract cookies from the currently running browser session.

    Use this after the user has logged in via noVNC but the automatic
    URL detection hasn't triggered.
    """
    if not _state["context"]:
        raise HTTPException(400, "No active browser session")
    if _state["platform"] not in SITES:
        raise HTTPException(400, f"Unknown platform: {_state['platform']}")

    site = SITES[_state["platform"]]
    _state["status"] = "extracting"
    _state["message"] = "Extracting cookies from running browser..."

    # Platform domain for disambiguation — e.g. instagram.com and
    # tiktok.com both set a "sessionid"; flattening by name alone picks
    # whichever cookie happened to come last and can poison the session.
    from urllib.parse import urlparse
    plat_domain = urlparse(site["url"]).hostname or ""
    plat_base = ".".join(plat_domain.split(".")[-2:])  # e.g. tiktok.com

    try:
        cookies = await _state["context"].cookies()
        all_cookies = {c["name"]: c["value"] for c in cookies}
        # Domain-scoped view: only cookies set on the platform's domain
        scoped = {
            c["name"]: c["value"]
            for c in cookies
            if c.get("domain", "").lstrip(".").endswith(plat_base)
        }

        # Save all cookies
        all_file = _cookie_file(f"{_state['platform']}_all_cookies.json")
        all_file.write_text(json.dumps(all_cookies, indent=2))

        # Extract target cookies — prefer the platform-domain value
        found = {}
        for name in site["cookies"]:
            value = scoped.get(name) or all_cookies.get(name)
            if value:
                found[name] = value
                cookie_file = _cookie_file(f"{_state['platform']}_{name}.txt")
                cookie_file.write_text(value)

        # Save storage state
        state_file = _cookie_file(f"{_state['platform']}_storage_state.json")
        await _state["context"].storage_state(path=str(state_file))
        _state["cookies"] = found
        _state["status"] = "done"
        _state["message"] = f"Extracted {len(found)}/{len(site['cookies'])} cookies"

        return {
            "platform": _state["platform"],
            "status": "done",
            "cookies": found,
            "cookies_found": list(found.keys()),
            "message": _state["message"],
        }
    except Exception as e:
        _state["status"] = "error"
        _state["message"] = f"Extraction error: {e}"
        raise HTTPException(500, str(e))


class ProfileUpdateRequest(BaseModel):
    full_name: str | None = None
    biography: str | None = None
    external_url: str | None = None


class NavigateRequest(BaseModel):
    url: str


class LoginRequest(BaseModel):
    username: str
    password: str
    verification_code: str | None = None


@app.post("/session/login")
async def login_session(req: LoginRequest):
    """Fill in login credentials on the current page and submit.

    Works for Instagram, Facebook, LinkedIn, etc. — finds username/password
    fields by common selectors and submits the form.
    """
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session — start one first")

    try:
        # Wait for page to be ready
        await page.wait_for_timeout(2000)

        # Try common username selectors across platforms
        username_selectors = [
            'input[name="username"]',
            'input[name="email"]',
            'input[type="email"]',
            'input[aria-label="Phone number, username, or email"]',
            'input[aria-label="Username"]',
            'input[aria-label="Email"]',
            'input[id="username"]',
            'input[id="email"]',
        ]
        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            'input[aria-label="Password"]',
            'input[id="password"]',
        ]

        # Find and fill username
        username_filled = False
        for sel in username_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.fill("")
                    await el.fill(req.username)
                    username_filled = True
                    break
            except Exception:
                continue

        if not username_filled:
            raise HTTPException(400, "Could not find username input field")

        # Find and fill password
        password_filled = False
        for sel in password_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    await el.fill("")
                    await el.fill(req.password)
                    password_filled = True
                    break
            except Exception:
                continue

        if not password_filled:
            raise HTTPException(400, "Could not find password input field")

        # Submit the form — try multiple methods
        submitted = False
        # Method 1: click submit button
        for sel in [
            'button[type="submit"]',
            'button:has-text("Log in")',
            'button:has-text("Log In")',
            'button:has-text("Sign in")',
            'button:has-text("Σύνδεση")',
            'div[role="button"]:has-text("Log in")',
            'div[role="button"]:has-text("Log In")',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    submitted = True
                    break
            except Exception:
                continue

        # Method 2: press Enter in the password field
        if not submitted:
            for sel in password_selectors:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        await el.press("Enter")
                        submitted = True
                        break
                except Exception:
                    continue

        if not submitted:
            raise HTTPException(400, "Could not submit login form")

        # Wait for navigation
        await page.wait_for_timeout(5000)

        # Check result
        current_url = page.url
        title = await page.title()

        # Detect login success vs failure
        body_text = await page.inner_text("body")
        is_logged_in = False
        challenge_required = False
        two_factor_required = False
        message = ""

        if "two-factor" in current_url or "2fa" in current_url or "challenge" in current_url:
            if "two-factor" in current_url or "2fa" in current_url:
                two_factor_required = True
                message = "2FA code required"
            else:
                challenge_required = True
                message = "Challenge required — check Instagram app or email"
        elif "login" in current_url and ("incorrect" in body_text.lower() or "invalid" in body_text.lower()):
            message = "Login failed — incorrect credentials"
        elif "login" not in current_url:
            is_logged_in = True
            message = "Login successful"
        else:
            message = f"Unclear status — URL: {current_url}"

        return {
            "status": "logged_in" if is_logged_in else "challenge" if challenge_required else "2fa" if two_factor_required else "unknown",
            "logged_in": is_logged_in,
            "challenge_required": challenge_required,
            "two_factor_required": two_factor_required,
            "url": current_url,
            "title": title,
            "message": message,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Login failed: {e}")


@app.post("/session/navigate")
async def navigate_session(req: NavigateRequest):
    """Navigate the active browser page to a URL."""
    page = await _ensure_live_page()
    try:
        await page.goto(req.url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        return {"status": "ok", "url": page.url, "title": await page.title()}
    except Exception as e:
        raise HTTPException(500, f"Navigation failed: {e}")


class EvaluateRequest(BaseModel):
    expression: str


@app.post("/session/evaluate")
async def evaluate_session(req: EvaluateRequest):
    """Run JavaScript in the active browser page and return the result."""
    page = await _ensure_live_page()
    try:
        result = await page.evaluate(req.expression)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(500, f"Evaluate failed: {e}")


@app.get("/session/page-info")
async def page_info():
    """Return current page URL and title."""
    page = await _ensure_live_page()
    try:
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        raise HTTPException(500, str(e))


class ClickRequest(BaseModel):
    selector: str
    text: str | None = None


@app.post("/session/click")
async def click_element(req: ClickRequest):
    """Click an element using Playwright's native click (not JS click).

    Use ``selector`` for a CSS selector, or ``text`` to click by text content.
    """
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session")
    try:
        if req.text:
            # Click by text within the selector
            locator = page.locator(req.selector, has_text=req.text).first
        else:
            locator = page.locator(req.selector).first
        count = await locator.count()
        if count == 0:
            raise HTTPException(404, f"Element not found: {req.selector}")
        await locator.click(force=True, timeout=10000)
        return {"status": "ok", "clicked": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Click failed: {e}")


class FillRequest(BaseModel):
    selector: str
    value: str


@app.post("/session/fill")
async def fill_field(req: FillRequest):
    """Fill an input field using Playwright's native fill (handles React)."""
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session")
    try:
        locator = page.locator(req.selector).first
        count = await locator.count()
        if count == 0:
            raise HTTPException(404, f"Element not found: {req.selector}")
        await locator.fill(req.value, timeout=10000)
        return {"status": "ok", "filled": True, "value": req.value}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Fill failed: {e}")


class MouseClickRequest(BaseModel):
    x: float
    y: float


@app.post("/session/mouse-click")
async def mouse_click(req: MouseClickRequest):
    """Click at exact viewport coordinates using Playwright mouse."""
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session")
    try:
        await page.mouse.click(req.x, req.y)
        return {"status": "ok", "clicked": True, "x": req.x, "y": req.y}
    except Exception as e:
        raise HTTPException(500, f"Mouse click failed: {e}")


class FileUploadRequest(BaseModel):
    selector: str
    file_path: str
    click_selector: str | None = None


@app.post("/session/upload")
async def upload_file(req: FileUploadRequest):
    """Upload a file to an input[type=file] element.

    If click_selector is provided, clicks that element first and intercepts
    the resulting filechooser event (needed for Instagram's photo upload).
    Otherwise, uses set_input_files directly on the selector.
    """
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session")
    try:
        if req.click_selector:
            # Set up filechooser listener BEFORE clicking
            async def handle_filechooser(files):
                await page.set_input_files(files, [req.file_path])

            page.on("filechooser", lambda fc: asyncio.create_task(fc.set_files(req.file_path)))

            # Click the element that triggers the file picker
            click_locator = page.locator(req.click_selector).first
            count = await click_locator.count()
            if count == 0:
                raise HTTPException(404, f"Click element not found: {req.click_selector}")
            await click_locator.click()
            await page.wait_for_timeout(5000)
            return {"status": "ok", "uploaded": True, "method": "filechooser", "click_selector": req.click_selector, "file": req.file_path}
        else:
            locator = page.locator(req.selector).first
            count = await locator.count()
            if count == 0:
                raise HTTPException(404, f"Element not found: {req.selector}")
            await locator.set_input_files(req.file_path)
            return {"status": "ok", "uploaded": True, "method": "set_input_files", "selector": req.selector, "file": req.file_path}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Upload failed: {e}")


class CookieRequest(BaseModel):
    cookies: list[dict]


@app.post("/session/cookies")
async def set_cookies(req: CookieRequest):
    """Add cookies to the browser context (for session injection)."""
    ctx = _state.get("context")
    if not ctx:
        raise HTTPException(400, "No active browser session")
    try:
        await ctx.add_cookies(req.cookies)
        return {"status": "ok", "added": len(req.cookies)}
    except Exception as e:
        raise HTTPException(500, f"Failed to set cookies: {e}")


@app.get("/profile/instagram")
async def get_instagram_profile():
    """Read the Instagram profile of the currently logged-in browser session.

    Navigates to the profile page and scrapes visible data (username, name,
    bio, category, follower counts).  Falls back to the edit page form
    fields if available.
    """
    page = _state.get("page")
    context = _state.get("context")
    if not page or not context:
        raise HTTPException(400, "No active browser session — start one first")

    try:
        # Navigate to own profile — Instagram redirects /accounts/edit/ to
        # login if not authenticated, so we go to the profile page first
        # to confirm we're logged in, then try the edit page.
        if "instagram.com" not in page.url:
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

        # Get username from the page (header section)
        result = await page.evaluate("""() => {
            const h2 = document.querySelector('h2');
            const header = document.querySelector('header');
            const body = document.body.innerText;
            const username = h2 ? h2.textContent.trim() : '';
            // Extract from body text
            const lines = body.split('\\n').filter(l => l.trim());
            return { username, body_lines: lines.slice(0, 30) };
        }""")

        username = result.get("username", "")

        # If we have a username, navigate to edit page via natural click
        if username:
            await _navigate_to_edit_profile(page, username)

            # Check if we're on the edit page (not redirected to login)
            if "login" in page.url or "__coig_login" in page.url:
                # Fallback: scrape from profile page
                await page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                scraped = await page.evaluate("""() => {
                    const body = document.body.innerText;
                    const lines = body.split('\\n');
                    return { body_lines: lines.slice(0, 30), raw: body.substring(0, 500) };
                }""")
                return {
                    "platform": "instagram",
                    "username": username,
                    "full_name": "",
                    "biography": "",
                    "external_url": "",
                    "profile_pic_url": "",
                    "scraped": scraped,
                }

            # Read form fields from the edit page
            form_data = await page.evaluate("""() => {
                const getVal = (sel) => {
                    const el = document.querySelector(sel);
                    return el ? el.value || '' : '';
                };
                // Try various selectors for the name field
                let fullName = '';
                for (const sel of ['input[name="first_name"]', 'input[aria-label="Name"]', 'input[aria-label="Όνομα"]', 'header + section input[type="text"]']) {
                    const v = getVal(sel);
                    if (v) { fullName = v; break; }
                }
                let bio = '';
                for (const sel of ['textarea[name="biography"]', 'textarea[aria-label="Bio"]', 'textarea']) {
                    const el = document.querySelector(sel);
                    if (el && el.tagName === 'TEXTAREA') { bio = el.value || ''; break; }
                }
                let url = '';
                for (const sel of ['input[name="external_url"]', 'input[aria-label="Website"]', 'input[aria-label="Ιστότοπος"]']) {
                    const v = getVal(sel);
                    if (v) { url = v; break; }
                }
                let picUrl = '';
                const img = document.querySelector('img[alt*="profile"]') || document.querySelector('header img');
                if (img) picUrl = img.src || '';
                return { full_name: fullName, biography: bio, external_url: url, profile_pic_url: picUrl };
            }""")

            return {
                "platform": "instagram",
                "username": username,
                **form_data,
            }

        # No username found — try the edit page directly
        await page.goto("https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if "login" in page.url:
            raise HTTPException(401, "Not logged in to Instagram")

        form_data = await page.evaluate("""() => {
            const getVal = (sel) => {
                const el = document.querySelector(sel);
                return el ? el.value || '' : '';
            };
            let fullName = '';
            for (const sel of ['input[name="first_name"]', 'input[aria-label="Name"]', 'input[aria-label="Όνομα"]']) {
                const v = getVal(sel);
                if (v) { fullName = v; break; }
            }
            let bio = '';
            const ta = document.querySelector('textarea[name="biography"]') || document.querySelector('textarea');
            if (ta) bio = ta.value || '';
            let url = getVal('input[name="external_url"]') || getVal('input[aria-label="Website"]');
            let picUrl = '';
            const img = document.querySelector('img[alt*="profile"]') || document.querySelector('header img');
            if (img) picUrl = img.src || '';
            return { full_name: fullName, biography: bio, external_url: url, profile_pic_url: picUrl };
        }""")

        return {"platform": "instagram", "username": "", **form_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to read profile: {e}")


async def _navigate_to_edit_profile(page, username: str | None = None):
    """Navigate to the Instagram edit profile page via natural click flow.

    Direct navigation to /accounts/edit/ triggers Instagram's __coig_login
    redirect (session invalidation guard).  Instead we navigate to the
    profile page and click the "Edit profile" button, which Instagram
    treats as a legitimate user action.
    """
    # Step 1: navigate to the profile page (this never triggers redirect)
    if username:
        profile_url = f"https://www.instagram.com/{username}/"
    else:
        profile_url = "https://www.instagram.com/"
    await page.goto(profile_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    if "login" in page.url:
        raise HTTPException(401, "Not logged in to Instagram")

    # Step 2: click the "Edit profile" button on the profile page
    edit_clicked = False
    for sel in [
        'a[href="/accounts/edit/"]',
        'a:has-text("Edit profile")',
        'div[role="button"]:has-text("Edit profile")',
    ]:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click()
                edit_clicked = True
                break
        except Exception:
            continue

    if not edit_clicked:
        # Fallback: direct navigation (may work if session is fresh)
        await page.goto(
            "https://www.instagram.com/accounts/edit/",
            wait_until="domcontentloaded",
        )

    await page.wait_for_timeout(3000)

    # Verify we landed on the edit page
    if "login" in page.url or "__coig_login" in page.url:
        raise HTTPException(
            401,
            "Instagram redirected to login — session may be stale. "
            "Re-login via VNC (/novnc/vnc.html) and retry.",
        )

    if "accounts/edit" not in page.url:
        raise HTTPException(
            500,
            f"Failed to reach edit profile page (current URL: {page.url})",
        )


@app.patch("/profile/instagram")
async def update_instagram_profile(req: ProfileUpdateRequest):
    """Update the Instagram profile via the logged-in browser session.

    Uses natural navigation (profile page → click "Edit profile") to avoid
    Instagram's __coig_login redirect guard.  Fills in the provided fields
    and clicks Submit.  Only fields that are provided (non-None) are changed.
    """
    page = _state.get("page")
    context = _state.get("context")
    if not page or not context:
        raise HTTPException(400, "No active browser session — start one first")

    try:
        # Detect username from current page if possible
        current_url = page.url
        username = None
        if _is_instagram_host(current_url):
            path = (urlparse(current_url).path or "/").strip("/")
            if path and not path.startswith("accounts"):
                parts = path.split("/")[0]
                if parts and not parts.startswith("?"):
                    username = parts

        # Natural navigation to edit profile page
        await _navigate_to_edit_profile(page, username)

        updated = []

        if req.full_name is not None:
            for sel in ['input[name="first_name"]', 'input[aria-label="Name"]', 'input[aria-label="Όνομα"]']:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        await el.fill("")
                        await el.fill(req.full_name)
                        updated.append("full_name")
                        break
                except Exception:
                    continue

        if req.biography is not None:
            for sel in ['textarea[name="biography"]', 'textarea[aria-label="Bio"]', 'textarea']:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0 and await el.evaluate("e => e.tagName") == "TEXTAREA":
                        await el.fill("")
                        await el.fill(req.biography)
                        updated.append("biography")
                        break
                except Exception:
                    continue

        if req.external_url is not None:
            for sel in ['input[name="external_url"]', 'input[aria-label="Website"]', 'input[aria-label="Ιστότοπος"]']:
                try:
                    el = page.locator(sel).first
                    if await el.count() > 0:
                        await el.fill("")
                        await el.fill(req.external_url)
                        updated.append("external_url")
                        break
                except Exception:
                    continue

        # Click submit
        submitted = False

        # Method 1: Playwright click with scroll-into-view
        for sel in [
            'div[role="button"]:has-text("Submit")',
            'div[role="button"]:has-text("Υποβολή")',
            'button[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Υποβολή")',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.scroll_into_view_if_needed(timeout=5000)
                    await btn.wait_for(state="visible", timeout=5000)
                    await btn.click(timeout=10000)
                    submitted = True
                    break
            except Exception:
                continue

        # Method 2: JS click fallback (more reliable for React apps)
        if not submitted:
            try:
                clicked = await page.evaluate("""() => {
                    const btns = document.querySelectorAll(
                        'div[role="button"], button[type="submit"]'
                    );
                    for (const b of btns) {
                        if (b.innerText.includes('Submit') || b.type === 'submit') {
                            b.scrollIntoView({block: 'center'});
                            b.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if clicked:
                    submitted = True
            except Exception:
                pass

        if not submitted:
            raise HTTPException(500, "Could not find submit button on edit page")

        await page.wait_for_timeout(3000)

        return {
            "platform": "instagram",
            "status": "updated",
            "updated_fields": updated,
            "submitted": submitted,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to update profile: {e}")


async def _run_browser(platform: str):
    """Background task: open browser, wait for login, extract cookies."""
    site = SITES[platform]
    try:
        async with async_playwright() as p:
            # Use persistent context so cookies survive between sessions
            user_data_dir = "/app/browser-profile"
            os.makedirs(user_data_dir, exist_ok=True)
            launch_kwargs = dict(
                headless=False,
                viewport={
                    "width": int(os.environ.get("SCREEN_WIDTH", 1280)),
                    "height": int(os.environ.get("SCREEN_HEIGHT", 800)),
                },
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--start-maximized",
                ],
            )
            try:
                context = await p.chromium.launch_persistent_context(
                    user_data_dir, **launch_kwargs
                )
            except Exception as launch_err:
                # A preempted session can leave an orphaned Chromium
                # holding the profile dir ("Opening in existing browser
                # session"). Kill strays and retry once before giving up.
                if "existing browser session" not in str(launch_err):
                    raise
                import subprocess

                subprocess.run(
                    ["pkill", "-f", "chromium"], capture_output=True
                )
                await asyncio.sleep(2)
                context = await p.chromium.launch_persistent_context(
                    user_data_dir, **launch_kwargs
                )
            browser = context.browser
            page = context.pages[0] if context.pages else await context.new_page()
            _state["browser"] = browser
            _state["context"] = context
            _state["page"] = page

            await page.goto(site["url"], wait_until="domcontentloaded")
            _state["message"] = f"Browser open at {site['url']} — log in via noVNC"

            # Wait for a success URL (up to 10 minutes). Poll page.url rather
            # than wait_for_url: the latter only fires on navigation events and
            # misses redirects that complete before the wait attaches (e.g. a
            # session whose persistent-profile cookies auto-authenticate the
            # login page instantly).
            success = False
            deadline = asyncio.get_event_loop().time() + 600
            while asyncio.get_event_loop().time() < deadline:
                try:
                    current_url = page.url
                except Exception:
                    break
                for pattern in site["success_patterns"]:
                    if pattern in current_url:
                        success = True
                        break
                if success:
                    break
                await asyncio.sleep(2)

            if success:
                _state["status"] = "extracting"
                _state["message"] = "Login detected — extracting cookies..."
                await asyncio.sleep(3)

                cookies = await context.cookies()
                all_cookies = {c["name"]: c["value"] for c in cookies}
                from urllib.parse import urlparse
                plat_base = ".".join(
                    (urlparse(site["url"]).hostname or "").split(".")[-2:]
                )
                scoped = {
                    c["name"]: c["value"]
                    for c in cookies
                    if c.get("domain", "").lstrip(".").endswith(plat_base)
                }

                # Save all cookies
                all_file = _cookie_file(f"{platform}_all_cookies.json")
                all_file.write_text(json.dumps(all_cookies, indent=2))

                # Extract target cookies — prefer the platform-domain value
                found = {}
                for name in site["cookies"]:
                    value = scoped.get(name) or all_cookies.get(name)
                    if value:
                        found[name] = value
                        cookie_file = _cookie_file(f"{platform}_{name}.txt")
                        cookie_file.write_text(value)

                # Save storage state
                state_file = _cookie_file(f"{platform}_storage_state.json")
                await context.storage_state(path=str(state_file))

                _state["cookies"] = found
                _state["status"] = "done"
                _state["message"] = f"Extracted {len(found)}/{len(site['cookies'])} cookies"
                # Keep the browser context open so profile reads/navigations work.
                # Exiting this coroutine's `async with async_playwright()` kills
                # Chromium — stay alive while this session is still current.
                # A new /session/start replaces _state["context"]; /session/stop
                # sets status away from done — either ends the hold.
                while (
                    _state.get("status") in ("done", "active")
                    and _state.get("context") is context
                ):
                    await asyncio.sleep(5)
                    try:
                        _ = context.pages  # raises once the context is closed
                    except Exception:
                        break
            else:
                _state["status"] = "error"
                _state["message"] = f"Timeout waiting for login. Current URL: {page.url}"
                await context.close()
                _state["browser"] = None
                _state["context"] = None

    except Exception as e:
        _state["status"] = "error"
        _state["message"] = f"Browser error: {e}"
        if _state.get("context"):
            try:
                await _state["context"].close()
            except Exception:
                pass
        _state["browser"] = None
        _state["context"] = None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BRIDGE_PORT", 9223)))
