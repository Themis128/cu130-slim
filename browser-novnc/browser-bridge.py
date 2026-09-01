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
        "success_patterns": ["https://www.instagram.com/", "instagram.com/accounts/one_tap"],
        "cookies": ["sessionid", "csrftoken", "ds_user_id", "ig_did", "mid", "rur"],
    },
    "facebook": {
        "url": "https://www.facebook.com/login",
        "success_patterns": ["https://www.facebook.com/", "facebook.com/?"],
        "cookies": ["c_user", "xs", "datr", "fr", "sb", "wd"],
    },
    "linkedin": {
        "url": "https://www.linkedin.com/login",
        "success_patterns": ["https://www.linkedin.com/feed", "linkedin.com/in/"],
        "cookies": ["li_at", "JSESSIONID", "liap", "bcookie", "bscookie", "lang"],
    },
    "tiktok": {
        "url": "https://www.tiktok.com/login",
        "success_patterns": ["https://www.tiktok.com/foryou", "tiktok.com/foryou"],
        "cookies": ["sessionid", "sid_tt", "uid_tt", "ttwid", "msToken", "passport_csrf_token"],
    },
    "twitter": {
        "url": "https://x.com/i/flow/login",
        "success_patterns": ["https://x.com/home", "x.com/home"],
        "cookies": ["auth_token", "ct0", "twid", "kdt", "guest_id"],
    },
    "threads": {
        "url": "https://www.threads.net/login",
        "success_patterns": ["https://www.threads.net/", "threads.net/@"],
        "cookies": ["sessionid", "csrftoken", "ds_user_id", "ig_did"],
    },
    "reddit": {
        "url": "https://www.reddit.com/login",
        "success_patterns": ["https://www.reddit.com/", "reddit.com/?"],
        "cookies": ["reddit_session", "token", "loid", "csv"],
    },
    "youtube": {
        "url": "https://accounts.google.com/v3/signin/identifier?continue=https://www.youtube.com",
        "success_patterns": ["https://www.youtube.com/", "youtube.com/feed"],
        "cookies": ["SAPISID", "SSID", "HSID", "APISID", "SID", "LOGIN_INFO", "__Secure-3PSID"],
    },
    "pinterest": {
        "url": "https://www.pinterest.com/login/",
        "success_patterns": ["https://www.pinterest.com/", "pinterest.com/?"],
        "cookies": ["_pinterest_sess", "csrftoken", "pinterest_ct", "auth_expires"],
    },
    "tumblr": {
        "url": "https://www.tumblr.com/login",
        "success_patterns": ["https://www.tumblr.com/dashboard", "tumblr.com/dashboard"],
        "cookies": ["pfs", "pfp", "user_props", "logging"],
    },
    "medium": {
        "url": "https://medium.com/m/signin",
        "success_patterns": ["https://medium.com/", "medium.com/me"],
        "cookies": ["sid", "uid", "sess", "__cf_bm"],
    },
    "discord": {
        "url": "https://discord.com/login",
        "success_patterns": ["https://discord.com/channels", "discord.com/channels"],
        "cookies": ["token", "discord_showcase", "__cfruid"],
    },
    "telegram": {
        "url": "https://web.telegram.org/a/",
        "success_patterns": ["https://web.telegram.org/a/", "telegram.org"],
        "cookies": ["stel_token", "tg_user", "sph_phone", "sph_hash"],
    },
    "whatsapp": {
        "url": "https://web.whatsapp.com/",
        "success_patterns": ["https://web.whatsapp.com/", "web.whatsapp.com"],
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
    """Return the noVNC web URL for embedding in an iframe."""
    port = os.environ.get("NOVNC_PORT", "6080")
    return {"url": f"http://localhost:{port}/vnc.html", "vnc_port": port}


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
        if _state["browser"]:
            try:
                await _state["browser"].close()
            except Exception:
                pass
            _state["browser"] = None
        _state["status"] = "idle"
        _state["platform"] = None
        _state["message"] = "Session stopped"
        return {"status": "stopped"}


async def _run_browser(platform: str):
    """Background task: open browser, wait for login, extract cookies."""
    site = SITES[platform]
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--start-maximized",
                ],
            )
            context = await browser.new_context(
                viewport={
                    "width": int(os.environ.get("SCREEN_WIDTH", 1280)),
                    "height": int(os.environ.get("SCREEN_HEIGHT", 800)),
                },
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
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

            await browser.close()
            _state["browser"] = None

    except Exception as e:
        _state["status"] = "error"
        _state["message"] = f"Browser error: {e}"
        if _state.get("browser"):
            try:
                await _state["browser"].close()
            except Exception:
                pass
        _state["browser"] = None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BRIDGE_PORT", 9223)))
