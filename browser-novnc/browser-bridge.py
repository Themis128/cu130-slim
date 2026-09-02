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
import json
import os
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

COOKIE_DIR = Path("/app/cookies")
COOKIE_DIR.mkdir(parents=True, exist_ok=True)

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
        "url": "https://www.threads.net/login",
        "success_patterns": ["threads.net/@", "threads.net/home"],
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
}

app = FastAPI(title="Browser Bridge", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartRequest(BaseModel):
    platform: str


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "novnc_url": f"http://localhost:{os.environ.get('NOVNC_PORT', 6080)}/vnc.html",
        "supported_platforms": list(SITES.keys()),
    }


@app.get("/novnc-url")
async def novnc_url():
    """Return the noVNC web URL (with audio support) for embedding in an iframe."""
    port = os.environ.get("NOVNC_PORT", "6080")
    return {"url": f"http://localhost:{port}/vnc-audio.html", "vnc_port": port}


@app.get("/platforms")
async def list_platforms():
    """List all supported platforms and their target cookies."""
    return {
        platform: {"url": site["url"], "cookies": site["cookies"]}
        for platform, site in SITES.items()
    }


@app.post("/session/start")
async def start_session(req: StartRequest):
    """Start a browser session for a platform — opens the login page."""
    async with _state["lock"]:
        if _state["status"] in ("waiting", "extracting"):
            raise HTTPException(409, f"Session already active for {_state['platform']}")

        platform = req.platform.lower()
        if platform not in SITES:
            raise HTTPException(400, f"Unknown platform: {platform}. Available: {list(SITES.keys())}")

        # Close any existing browser
        if _state["browser"]:
            try:
                await _state["browser"].close()
            except Exception:
                pass
            _state["browser"] = None

        site = SITES[platform]
        _state["platform"] = platform
        _state["status"] = "waiting"
        _state["message"] = f"Opening {site['url']} — log in via the noVNC viewer"
        _state["cookies"] = {}

        # Start browser in background
        asyncio.create_task(_run_browser(platform))

        return {
            "platform": platform,
            "status": "waiting",
            "message": _state["message"],
            "novnc_url": f"http://localhost:{os.environ.get('NOVNC_PORT', '6080')}/vnc.html",
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
        _state["message"] = "Session stopped"
        return {"status": "stopped"}


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

    try:
        cookies = await _state["context"].cookies()
        all_cookies = {c["name"]: c["value"] for c in cookies}

        # Save all cookies
        all_file = COOKIE_DIR / f"{_state['platform']}_all_cookies.json"
        all_file.write_text(json.dumps(all_cookies, indent=2))

        # Extract target cookies
        found = {}
        for name in site["cookies"]:
            if name in all_cookies:
                found[name] = all_cookies[name]
                cookie_file = COOKIE_DIR / f"{_state['platform']}_{name}.txt"
                cookie_file.write_text(all_cookies[name])

        # Save storage state
        state_file = COOKIE_DIR / f"{_state['platform']}_storage_state.json"
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
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session")
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
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session")
    try:
        result = await page.evaluate(req.expression)
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(500, f"Evaluate failed: {e}")


@app.get("/session/page-info")
async def page_info():
    """Return current page URL and title."""
    page = _state.get("page")
    if not page:
        raise HTTPException(400, "No active browser session")
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

        # If we have a username, navigate to the edit page
        if username:
            await page.goto(f"https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # Check if we're on the edit page (not redirected to login)
            if "login" in page.url:
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


@app.patch("/profile/instagram")
async def update_instagram_profile(req: ProfileUpdateRequest):
    """Update the Instagram profile via the logged-in browser session.

    Navigates to the Instagram edit profile page, fills in the provided
    fields, and clicks Submit.  Only fields that are provided (non-None)
    are changed.
    """
    page = _state.get("page")
    context = _state.get("context")
    if not page or not context:
        raise HTTPException(400, "No active browser session — start one first")

    try:
        # Navigate to the edit page
        await page.goto("https://www.instagram.com/accounts/edit/", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        if "login" in page.url:
            raise HTTPException(401, "Not logged in to Instagram")

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
        for sel in [
            'button[type="submit"]',
            'div[role="button"]:has-text("Submit")',
            'div[role="button"]:has-text("Υποβολή")',
            'button:has-text("Submit")',
            'button:has-text("Υποβολή")',
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    submitted = True
                    break
            except Exception:
                continue

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
            context = await p.chromium.launch_persistent_context(
                user_data_dir,
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
            browser = context.browser
            page = context.pages[0] if context.pages else await context.new_page()
            _state["browser"] = browser
            _state["context"] = context
            _state["page"] = page

            await page.goto(site["url"], wait_until="domcontentloaded")
            _state["message"] = f"Browser open at {site['url']} — log in via noVNC"

            # Wait for success URL (up to 10 minutes)
            success = False
            for pattern in site["success_patterns"]:
                try:
                    await page.wait_for_url(f"{pattern}**", timeout=600000)
                    success = True
                    break
                except Exception:
                    continue

            if not success:
                current_url = page.url
                for pattern in site["success_patterns"]:
                    if pattern in current_url:
                        success = True
                        break

            if success:
                _state["status"] = "extracting"
                _state["message"] = "Login detected — extracting cookies..."
                await asyncio.sleep(3)

                cookies = await context.cookies()
                all_cookies = {c["name"]: c["value"] for c in cookies}

                # Save all cookies
                all_file = COOKIE_DIR / f"{platform}_all_cookies.json"
                all_file.write_text(json.dumps(all_cookies, indent=2))

                # Extract target cookies
                found = {}
                for name in site["cookies"]:
                    if name in all_cookies:
                        found[name] = all_cookies[name]
                        cookie_file = COOKIE_DIR / f"{platform}_{name}.txt"
                        cookie_file.write_text(all_cookies[name])

                # Save storage state
                state_file = COOKIE_DIR / f"{platform}_storage_state.json"
                await context.storage_state(path=str(state_file))

                _state["cookies"] = found
                _state["status"] = "done"
                _state["message"] = f"Extracted {len(found)}/{len(site['cookies'])} cookies"
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
