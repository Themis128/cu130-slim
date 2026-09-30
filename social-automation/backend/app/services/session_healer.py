"""Self-healing session manager — keeps every social platform connected.

Runs from the ``heal_sessions`` Celery task (hourly) and on demand via
``POST /api/v1/ops/session-heal``. For each transport layer it probes the
live session, attempts the cheapest recovery path, persists fresh cookies
to Postgres/volumes, and alerts Slack only when human action is required.

Recovery ladder per platform (cheapest → most invasive):

- **linkedin** (sidecar :9225)
    1. Clear the in-memory 429 circuit if stale (>6h past expiry).
    2. ``GET /session`` probe — healthy → export cookies → sync
       ``meta_data.browser_storage_state`` on all linkedin accounts.
    3. Dead → credential ``POST /login`` (``LINKEDIN_EMAIL`` /
       ``LINKEDIN_PASSWORD``). LinkedIn answers with an app push /
       SMS 2FA checkpoint; the page auto-advances when the owner taps
       "Yes" in the LinkedIn app, so we poll briefly then park a
       ``pending_2fa`` flag and alert once. Cross-browser cookie
       transplant does NOT work — LinkedIn fingerprints ``li_at`` and
       revokes it globally when it shows up under a different browser.
- **facebook** (sidecar :9226)
    1. ``GET /session/validate`` deep-check → healthy → export + sync.
    2. Dead → re-inject stored ``browser_storage_state`` (same-browser
       restores are accepted — unlike LinkedIn) → re-validate → alert
       noVNC if still dead.
- **bridge platforms** (facebook, instagram, threads, twitter, tiktok on
  the shared browser-novnc :9223)
    1. ``POST /session/start`` (X-Platform tagged) — the persistent
       profile often still holds cookies; the detection loop flips the
       session to ``done`` within seconds.
    2. Still waiting → platform recovery:
       - ``threads``: Instagram-SSO bootstrap — navigate the IG-SSO login
         variant, click "Continue with Instagram" + the account card.
       - ``instagram``/``twitter``: ``/session/login`` with stored
         credentials from the secret store.
       - ``facebook``/``tiktok``: inject stored cookies
         (``browser_storage_state`` / ``TIKTOK_SESSION_ID``) via
         ``POST /session/cookies`` then navigate-verify.
    3. Still waiting → Slack alert with the noVNC URL (24h cooldown).

Every probe honours the bridge busy-hold protocol (X-Platform header,
409 = contended → skipped this run) and never logs secret material —
cookie names only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.social_account import SocialAccount

logger = logging.getLogger(__name__)

LINKEDIN_SIDECAR_URL = "http://linkedin-browser-sidecar:9225"
FACEBOOK_SIDECAR_URL = "http://facebook-browser-sidecar:9226"
BRIDGE_URL = "http://browser-novnc:9223"

_ALERT_TTL = 24 * 3600          # Slack cooldown per platform
_LI_PENDING_KEY = "session_healer:linkedin_pending_2fa"
_ALERT_KEY = "session_healer:alerted:{platform}"

# Bridge platforms and how we verify a logged-in state after navigation.
# login_marker = JS that is truthy ONLY on the logged-out/login page.
BRIDGE_PLATFORMS: dict[str, dict[str, str]] = {
    "facebook": {
        "nav": "https://www.facebook.com/",
        "login_marker": '!!document.querySelector("input[name=email]")',
    },
    "instagram": {
        "nav": "https://www.instagram.com/",
        "login_marker": '!!document.querySelector("input[name=username]")',
    },
    "threads": {
        "nav": "https://www.threads.net/",
        "login_marker": 'location.pathname.includes("/login")',
    },
    "twitter": {
        "nav": "https://x.com/home",
        "login_marker": 'location.pathname.includes("/i/flow/login") || !!document.querySelector("input[autocomplete=username]")',
    },
    "tiktok": {
        "nav": "https://www.tiktok.com/",
        "login_marker": '!!document.querySelector("[data-e2e=login-button]") && !document.querySelector("[data-e2e=profile-icon]")',
    },
}

THREADS_SSO_URL = (
    "https://www.threads.com/login/?show_toa_choice_screen=false&variant=toa_ig"
)


async def _get_redis() -> Any:
    import redis.asyncio as aioredis

    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _alert(platform: str, text: str) -> None:
    """Slack-alert once per platform per 24h while it stays broken."""
    try:
        r = await _get_redis()
        try:
            if await r.set(_ALERT_KEY.format(platform=platform), "1", nx=True, ex=_ALERT_TTL):
                from app.services.slack_notifications import post_alert_to_slack

                await post_alert_to_slack(text[:2000])
        finally:
            await r.aclose()
    except Exception:
        logger.debug("session healer alert failed for %s", platform, exc_info=True)


async def _alert_recovered(platform: str, text: str) -> None:
    """Notify once when a previously-broken platform comes back."""
    try:
        r = await _get_redis()
        try:
            had_alert = await r.delete(_ALERT_KEY.format(platform=platform))
            if had_alert:
                from app.services.slack_notifications import post_alert_to_slack

                await post_alert_to_slack(text[:2000])
        finally:
            await r.aclose()
    except Exception:
        logger.debug("session healer recovery notice failed for %s", platform, exc_info=True)


async def _secret(name: str) -> str:
    """Read a login credential from the secret store (never logs values)."""
    try:
        from app.services.secret_store import secret_store

        return (await secret_store.get(name)) or ""
    except Exception:
        return ""


def _env(name: str) -> str:
    return os.environ.get(name, "")


# ── DB sync helpers ────────────────────────────────────────────────────────


async def _sync_storage_state(platform: str, storage_state: dict) -> int:
    """Persist a Playwright storage_state to all accounts of a platform."""
    updated = 0
    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == platform)
        )
        for account in result.scalars().all():
            meta = dict(account.meta_data or {})
            meta["browser_storage_state"] = storage_state
            meta["session_healed_at"] = datetime.now(UTC).isoformat()
            account.meta_data = meta
            flag_modified(account, "meta_data")
            updated += 1
        await db.commit()
    return updated


async def _sync_browser_cookies(platform: str, cookies: dict[str, str]) -> int:
    """Persist a name→value cookie map on all accounts of a platform."""
    updated = 0
    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == platform)
        )
        for account in result.scalars().all():
            meta = dict(account.meta_data or {})
            meta["browser_cookies"] = cookies
            meta["session_healed_at"] = datetime.now(UTC).isoformat()
            account.meta_data = meta
            flag_modified(account, "meta_data")
            updated += 1
        await db.commit()
    return updated


async def _load_storage_state(platform: str) -> dict | None:
    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.platform == platform)
        )
        for account in result.scalars().all():
            state = (account.meta_data or {}).get("browser_storage_state")
            if state and state.get("cookies"):
                return state
    return None


async def _platforms_with_accounts() -> set[str]:
    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount.platform).where(
                SocialAccount.status.in_(["active", "expired"])
            )
        )
        return {row[0] for row in result.all()}


async def _account_username(platform: str) -> str:
    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount.username).where(
                SocialAccount.platform == platform,
                SocialAccount.status == "active",
            )
        )
        return result.scalars().first() or ""


# ── LinkedIn sidecar ───────────────────────────────────────────────────────


async def _li_sync_cookies() -> None:
    """Export the live cookie jar and persist to both LinkedIn accounts."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{LINKEDIN_SIDECAR_URL}/debug/all-cookies")
        resp.raise_for_status()
        cookies = resp.json().get("cookies", {})
    if not cookies.get("li_at"):
        return
    cookie_list = [
        {
            "name": n,
            "value": v,
            "domain": ".linkedin.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
        for n, v in cookies.items()
    ]
    await _sync_storage_state("linkedin", {"cookies": cookie_list, "origins": []})
    try:
        from app.services.secret_store import secret_store

        await secret_store.set(
            "LINKEDIN_COOKIE", cookies["li_at"], "LinkedIn li_at session cookie (auto-refreshed)"
        )
    except Exception:
        logger.debug("LINKEDIN_COOKIE store update failed", exc_info=True)


async def _li_logged_in() -> bool:
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.get(f"{LINKEDIN_SIDECAR_URL}/session")
        return bool(resp.json().get("logged_in"))


async def _heal_linkedin(stats: dict) -> None:
    r = await _get_redis()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            health = (await client.get(f"{LINKEDIN_SIDECAR_URL}/health")).json()

        stats["linkedin"]["sidecar_up"] = health.get("status") == "ok"
        if not stats["linkedin"]["sidecar_up"]:
            stats["linkedin"]["status"] = "sidecar_down"
            await _alert(
                "linkedin",
                "*LinkedIn sidecar is down* — browser automation paused.\n"
                "• Fix: `docker compose restart linkedin-browser-sidecar`",
            )
            return

        # Clear a stale in-memory 429 circuit (>6h past expiry).
        until = health.get("rate_limit_until")
        if health.get("rate_limited") and until:
            age = time.time() * 1000 - until
            if age > 6 * 3600 * 1000:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    await client.post(f"{LINKEDIN_SIDECAR_URL}/session/clear-rate-limit")
                stats["linkedin"]["rate_limit_cleared"] = True
            else:
                stats["linkedin"]["status"] = "rate_limited"
                return

        if await _li_logged_in():
            stats["linkedin"]["status"] = "healthy"
            await _li_sync_cookies()
            await r.delete(_LI_PENDING_KEY)
            await _alert_recovered(
                "linkedin", "*LinkedIn session is back* — sidecar logged in again."
            )
            return

        # A previous /login may be sitting on an approved 2FA checkpoint —
        # the probe above already re-checked it, so fall through to login.
        email = _env("LINKEDIN_EMAIL")
        password = _env("LINKEDIN_PASSWORD") or await _secret("LINKEDIN_PASSWORD")
        if not email or not password:
            stats["linkedin"]["status"] = "needs_manual_login"
            await _alert(
                "linkedin",
                "*LinkedIn session expired and no credentials are configured* — "
                "log in via the sidecar or set LINKEDIN_EMAIL/LINKEDIN_PASSWORD.",
            )
            return

        pending = await r.get(_LI_PENDING_KEY)
        if pending:
            # Already alerted and an /login checkpoint is outstanding —
            # don't re-trigger (each retry sends a new SMS/push).
            stats["linkedin"]["status"] = "pending_2fa"
            return

        async with httpx.AsyncClient(timeout=150.0) as client:
            resp = await client.post(
                f"{LINKEDIN_SIDECAR_URL}/login",
                json={"username": email, "password": password},
            )
            login = resp.json()

        if login.get("logged_in"):
            stats["linkedin"]["status"] = "recovered"
            await _li_sync_cookies()
            return

        if login.get("two_factor_required"):
            # LinkedIn usually pushes to the LinkedIn mobile app — the
            # checkpoint page auto-advances when the owner taps "Yes".
            for _ in range(18):
                await asyncio.sleep(5)
                if await _li_logged_in():
                    stats["linkedin"]["status"] = "recovered"
                    await _li_sync_cookies()
                    await _alert_recovered(
                        "linkedin",
                        "*LinkedIn session recovered* — 2FA approved, logged in.",
                    )
                    return
            await r.set(_LI_PENDING_KEY, "1", ex=_ALERT_TTL)
            stats["linkedin"]["status"] = "pending_2fa"
            await _alert(
                "linkedin",
                "*LinkedIn needs a verification step*\n"
                "• A sign-in was attempted — approve it in the LinkedIn mobile app.\n"
                "• If it asks for a code instead, share the SMS code and I'll submit it.",
            )
            return

        stats["linkedin"]["status"] = "needs_manual_login"
        await _alert(
            "linkedin",
            "*LinkedIn login failed* — "
            f"`{str(login.get('error') or login.get('message'))[:200]}`\n"
            "• Log in via the sidecar or check stored credentials.",
        )
    except Exception as exc:
        stats["linkedin"]["status"] = "error"
        stats["linkedin"]["error"] = str(exc)[:200]
        logger.warning("LinkedIn heal failed: %s", exc, exc_info=True)
    finally:
        await r.aclose()


# ── Facebook sidecar ───────────────────────────────────────────────────────


async def _heal_facebook_sidecar(stats: dict) -> None:
    slot = stats["facebook_sidecar"]
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.get(f"{FACEBOOK_SIDECAR_URL}/session/validate")
            data = resp.json()

        if data.get("logged_in") and not data.get("profile_picker"):
            slot["status"] = "healthy"
            async with httpx.AsyncClient(timeout=30.0) as client:
                cookies = (await client.get(f"{FACEBOOK_SIDECAR_URL}/profile/cookies")).json()
            jar = cookies.get("cookies", cookies)
            if isinstance(jar, dict) and jar:
                cookie_list = [
                    {"name": n, "value": v, "domain": ".facebook.com", "path": "/",
                     "secure": True, "sameSite": "Lax"}
                    for n, v in jar.items()
                ]
                slot["accounts_synced"] = await _sync_storage_state(
                    "facebook", {"cookies": cookie_list, "origins": []}
                )
            await _alert_recovered(
                "facebook_sidecar", "*Facebook sidecar session is back* — logged in again."
            )
            return

        # Dead → try re-injecting the stored storage_state (same-browser
        # restores work on Facebook, unlike LinkedIn's fingerprinted li_at).
        state = await _load_storage_state("facebook")
        if state:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{FACEBOOK_SIDECAR_URL}/session",
                    json={"storage_state": state, "verify": True},
                )
                result = resp.json()
            if result.get("logged_in"):
                slot["status"] = "recovered"
                await _alert_recovered(
                    "facebook_sidecar",
                    "*Facebook sidecar session recovered* via stored cookies.",
                )
                return

        slot["status"] = "needs_manual_login"
        await _alert(
            "facebook_sidecar",
            "*Facebook sidecar session is dead* — stored cookies rejected.\n"
            "• Fix: log in via the shared browser bridge (noVNC) so the "
            "persistent profile refreshes, or re-run the session transplant.",
        )
    except Exception as exc:
        slot["status"] = "error"
        slot["error"] = str(exc)[:200]
        logger.warning("Facebook sidecar heal failed: %s", exc, exc_info=True)


# ── Shared browser bridge ──────────────────────────────────────────────────


async def _bridge_call(
    path: str,
    platform: str,
    body: dict | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict]:
    """POST (or GET) a bridge endpoint, tagged with X-Platform."""
    method = "POST" if body is not None else "GET"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(
            method,
            f"{BRIDGE_URL}{path}",
            json=body,
            headers={"X-Platform": platform},
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {}


async def _bridge_eval(platform: str, expression: str) -> Any:
    code, data = await _bridge_call(
        "/session/evaluate", platform, {"expression": expression}, timeout=30.0
    )
    if code == 200:
        return data.get("result")
    return None


async def _bridge_poll_done(platform: str, seconds: int = 45) -> bool:
    """Wait for the bridge detection loop to flip the session to done."""
    for _ in range(seconds // 3):
        _, status = await _bridge_call("/session/status", platform, timeout=15.0)
        if (
            status.get("platform") == platform
            and status.get("status") in ("active", "done")
            and status.get("cookies_found")
        ):
            return True
        await asyncio.sleep(3)
    return False


async def _bridge_extract(platform: str) -> bool:
    code, data = await _bridge_call("/session/extract", platform, {}, timeout=60.0)
    found = data.get("cookies_found") or list((data.get("cookies") or {}).keys())
    if code == 200 and found:
        cookies = data.get("cookies") or {}
        if cookies:
            await _sync_browser_cookies(platform, cookies)
        return True
    return False


async def _bridge_verify_not_login(platform: str) -> bool:
    """Navigate the platform home and confirm we are NOT on a login page."""
    nav = BRIDGE_PLATFORMS[platform]["nav"]
    code, _ = await _bridge_call(
        "/session/navigate", platform, {"url": nav}, timeout=90.0
    )
    if code != 200:
        return False
    await asyncio.sleep(3)
    marker = await _bridge_eval(platform, BRIDGE_PLATFORMS[platform]["login_marker"])
    return marker is False  # marker truthy means still on the login page


async def _bootstrap_threads_via_instagram() -> bool:
    """Drive the Threads IG-SSO flow: login → Continue with Instagram →
    account card → feed. Requires a live instagram session in the same
    shared browser profile."""
    code, _ = await _bridge_call(
        "/session/navigate",
        "threads",
        {"url": THREADS_SSO_URL},
        timeout=90.0,
    )
    if code != 200:
        return False
    await asyncio.sleep(5)
    # Dismiss a cookie banner if present, then click the SSO button.
    await _bridge_eval(
        "threads",
        "(()=>{const b=[...document.querySelectorAll('button')].find(x=>/allow|accept|decline optional/i.test(x.textContent));if(b)b.click();return true})()",
    )
    await asyncio.sleep(2)
    code, _ = await _bridge_call(
        "/session/click",
        "threads",
        {"selector": "div[role=button],button,a", "text": "Continue with Instagram"},
        timeout=30.0,
    )
    if code != 200:
        return False
    await asyncio.sleep(6)
    # SSO page renders the Instagram account card — click it.
    text = await _bridge_eval("threads", "document.body.innerText.slice(0,500)")
    if not text or "Threads" not in str(text):
        return False
    code, _ = await _bridge_call(
        "/session/click",
        "threads",
        {"selector": "div[role=button]", "text": "Continue to Threads"},
        timeout=30.0,
    )
    if code != 200:
        # Fallback: click the account card (shows the IG handle).
        username = await _account_username("instagram") or "instagram"
        code, _ = await _bridge_call(
            "/session/click",
            "threads",
            {"selector": "div[role=button],button,a", "text": username.lstrip("@")},
            timeout=30.0,
        )
        if code != 200:
            return False
    await asyncio.sleep(8)
    marker = await _bridge_eval("threads", BRIDGE_PLATFORMS["threads"]["login_marker"])
    return marker is False


async def _bridge_login(platform: str, username: str, password: str) -> bool:
    """Credential login through the bridge's /session/login handler.

    The endpoint fills username/password on the *current* page — callers
    must already have ``/session/start``ed the platform so its login page
    is loaded.
    """
    code, data = await _bridge_call(
        "/session/login",
        platform,
        {"username": username, "password": password},
        timeout=180.0,
    )
    if code != 200:
        return False
    status = str(data.get("status") or "")
    return status in ("logged_in", "active", "done") or bool(data.get("logged_in"))


async def _bridge_inject_cookies(platform: str, cookies: list[dict]) -> bool:
    code, data = await _bridge_call(
        "/session/cookies", platform, {"cookies": cookies}, timeout=30.0
    )
    return code == 200 and bool(data.get("added") or data.get("status") == "ok")


async def _recover_bridge_platform(platform: str) -> bool:
    """Platform-specific recovery after a session/start left us waiting."""
    if platform == "threads":
        return await _bootstrap_threads_via_instagram()

    if platform == "facebook":
        state = await _load_storage_state("facebook")
        if state and state.get("cookies"):
            if await _bridge_inject_cookies(platform, state["cookies"]):
                return await _bridge_verify_not_login(platform)
        return False

    if platform == "tiktok":
        session_id = await _secret("TIKTOK_SESSION_ID")
        if session_id:
            ok = await _bridge_inject_cookies(
                platform,
                [{"name": "sessionid", "value": session_id,
                  "domain": ".tiktok.com", "path": "/", "secure": True}],
            )
            if ok:
                return await _bridge_verify_not_login(platform)
        return False

    if platform == "instagram":
        username = _env("INSTAGRAM_USERNAME") or await _secret("INSTAGRAM_USERNAME")
        password = _env("INSTAGRAM_PASSWORD") or await _secret("INSTAGRAM_PASSWORD")
        if username and password:
            return await _bridge_login(platform, username, password)
        return False

    if platform == "twitter":
        password = _env("TWITTER_LOGIN_PASSWORD") or await _secret("TWITTER_LOGIN_PASSWORD")
        username = await _account_username("twitter")
        if username and password:
            return await _bridge_login(platform, username.lstrip("@"), password)
        return False

    return False


async def _heal_bridge_platform(platform: str, stats: dict) -> None:
    slot = stats.setdefault(platform, {})
    try:
        # Current session already belongs to this platform and is done.
        _, status = await _bridge_call("/session/status", platform, timeout=15.0)
        if (
            status.get("platform") == platform
            and status.get("status") in ("active", "done")
            and status.get("cookies_found")
        ):
            slot["status"] = "healthy"
            return

        code, started = await _bridge_call(
            "/session/start", platform, {"platform": platform}, timeout=60.0
        )
        if code == 409:
            slot["status"] = "contended"
            return
        if code >= 400:
            slot["status"] = "error"
            slot["error"] = str(started)[:200]
            return

        # The persistent profile may already hold cookies — the detection
        # loop flips waiting → done within seconds in that case.
        if await _bridge_poll_done(platform):
            if await _bridge_extract(platform):
                slot["status"] = "healthy"
                await _alert_recovered(
                    platform, f"*{platform.title()} bridge session is back* — logged in again."
                )
                return

        # Still waiting → attempt platform-specific recovery.
        if await _recover_bridge_platform(platform):
            await asyncio.sleep(3)
            if await _bridge_poll_done(platform, seconds=20):
                await _bridge_extract(platform)
                slot["status"] = "recovered"
                await _alert_recovered(
                    platform,
                    f"*{platform.title()} bridge session recovered* automatically.",
                )
                return
            # Recovery flow succeeded but detection hasn't flipped yet —
            # verify explicitly before giving up.
            if await _bridge_verify_not_login(platform):
                await _bridge_extract(platform)
                slot["status"] = "recovered"
                await _alert_recovered(
                    platform,
                    f"*{platform.title()} bridge session recovered* automatically.",
                )
                return

        slot["status"] = "needs_manual_login"
        await _alert(
            platform,
            f"*{platform.title()} browser session needs a manual login*\n"
            "• Open the noVNC viewer and log in: http://localhost:6080/novnc/vnc.html\n"
            f"• Platform: `{platform}` — the bridge will detect and persist the session.",
        )
    except Exception as exc:
        slot["status"] = "error"
        slot["error"] = str(exc)[:200]
        logger.warning("Bridge heal failed for %s: %s", platform, exc, exc_info=True)


# ── Orchestrator ───────────────────────────────────────────────────────────


async def heal_all_sessions() -> dict[str, Any]:
    """Sweep all browser transports and heal what can be healed."""
    stats: dict[str, Any] = {
        "linkedin": {},
        "facebook_sidecar": {},
        "checked_at": datetime.now(UTC).isoformat(),
    }

    # Distributed lock — overlapping heal runs would fight over the shared
    # bridge and double-fire LinkedIn /login (each sends a fresh push/SMS).
    r = await _get_redis()
    try:
        if not await r.set("session_healer:lock", "1", nx=True, ex=1500):
            stats["skipped"] = "another heal run in progress"
            return stats
    finally:
        await r.aclose()

    try:
        accounts = await _platforms_with_accounts()

        if "linkedin" in accounts:
            await _heal_linkedin(stats)
        if "facebook" in accounts:
            await _heal_facebook_sidecar(stats)

        for platform in ("facebook", "instagram", "threads", "twitter", "tiktok"):
            if platform in accounts:
                await _heal_bridge_platform(platform, stats)
    finally:
        try:
            r = await _get_redis()
            await r.delete("session_healer:lock")
            await r.aclose()
        except Exception:
            pass

    stats["unhealthy"] = [
        k for k, v in stats.items()
        if isinstance(v, dict) and v.get("status") in (
            "needs_manual_login", "pending_2fa", "sidecar_down", "error",
        )
    ]
    logger.info("Session heal complete: %s", stats)
    return stats
