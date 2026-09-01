#!/usr/bin/env python3
"""Extract session cookies from ANY social platform via headed Playwright.

Usage:
    python3 extract-cookies.py instagram
    python3 extract-cookies.py facebook
    python3 extract-cookies.py linkedin
    python3 extract-cookies.py tiktok
    python3 extract-cookies.py twitter
    python3 extract-cookies.py threads
    python3 extract-cookies.py reddit
    python3 extract-cookies.py youtube
    python3 extract-cookies.py pinterest
    python3 extract-cookies.py <url>

After you log in visually (via noVNC), this script waits for the login
to complete, extracts ALL cookies, and saves them to files.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

COOKIE_DIR = Path("/app/cookies")
COOKIE_DIR.mkdir(parents=True, exist_ok=True)

SITES = {
    "instagram": {
        "url": "https://www.instagram.com/accounts/login/",
        "success_patterns": ["https://www.instagram.com/", "instagram.com/accounts/one_tap"],
        "cookies": ["sessionid", "csrftoken", "ds_user_id", "ig_did", "mid", "rur"],
        "prefix": "instagram",
    },
    "facebook": {
        "url": "https://www.facebook.com/login",
        "success_patterns": ["https://www.facebook.com/", "facebook.com/?"],
        "cookies": ["c_user", "xs", "datr", "fr", "sb", "wd"],
        "prefix": "facebook",
    },
    "linkedin": {
        "url": "https://www.linkedin.com/login",
        "success_patterns": ["https://www.linkedin.com/feed", "linkedin.com/in/"],
        "cookies": ["li_at", "JSESSIONID", "liap", "bcookie", "bscookie", "lang"],
        "prefix": "linkedin",
    },
    "tiktok": {
        "url": "https://www.tiktok.com/login",
        "success_patterns": ["https://www.tiktok.com/foryou", "tiktok.com/foryou"],
        "cookies": ["sessionid", "sid_tt", "uid_tt", "ttwid", "msToken", "passport_csrf_token"],
        "prefix": "tiktok",
    },
    "twitter": {
        "url": "https://x.com/i/flow/login",
        "success_patterns": ["https://x.com/home", "x.com/home"],
        "cookies": ["auth_token", "ct0", "twid", "kdt", "guest_id"],
        "prefix": "twitter",
    },
    "threads": {
        "url": "https://www.threads.net/login",
        "success_patterns": ["https://www.threads.net/", "threads.net/@"],
        "cookies": ["sessionid", "csrftoken", "ds_user_id", "ig_did"],
        "prefix": "threads",
    },
    "reddit": {
        "url": "https://www.reddit.com/login",
        "success_patterns": ["https://www.reddit.com/", "reddit.com/?"],
        "cookies": ["reddit_session", "token", "loid", "csv"],
        "prefix": "reddit",
    },
    "youtube": {
        "url": "https://accounts.google.com/v3/signin/identifier?continue=https://www.youtube.com",
        "success_patterns": ["https://www.youtube.com/", "youtube.com/feed"],
        "cookies": ["SAPISID", "SSID", "HSID", "APISID", "SID", "LOGIN_INFO", "__Secure-3PSID"],
        "prefix": "youtube",
    },
    "pinterest": {
        "url": "https://www.pinterest.com/login/",
        "success_patterns": ["https://www.pinterest.com/", "pinterest.com/?"],
        "cookies": ["_pinterest_sess", "csrftoken", "pinterest_ct", "auth_expires"],
        "prefix": "pinterest",
    },
    "tumblr": {
        "url": "https://www.tumblr.com/login",
        "success_patterns": ["https://www.tumblr.com/dashboard", "tumblr.com/dashboard"],
        "cookies": ["pfs", "pfp", "user_props", "logging"],
        "prefix": "tumblr",
    },
    "medium": {
        "url": "https://medium.com/m/signin",
        "success_patterns": ["https://medium.com/", "medium.com/me"],
        "cookies": ["sid", "uid", "sess", "__cf_bm"],
        "prefix": "medium",
    },
    "discord": {
        "url": "https://discord.com/login",
        "success_patterns": ["https://discord.com/channels", "discord.com/channels"],
        "cookies": ["token", "discord_showcase", "__cfruid"],
        "prefix": "discord",
    },
    "telegram": {
        "url": "https://web.telegram.org/a/",
        "success_patterns": ["https://web.telegram.org/a/", "telegram.org"],
        "cookies": ["stel_token", "tg_user", "sph_phone", "sph_hash"],
        "prefix": "telegram",
    },
    "whatsapp": {
        "url": "https://web.whatsapp.com/",
        "success_patterns": ["https://web.whatsapp.com/", "web.whatsapp.com"],
        "cookies": ["wa_web_prefs", "wa_csrf_token"],
        "prefix": "whatsapp",
    },
}


async def main():
    site_key = sys.argv[1] if len(sys.argv) > 1 else "instagram"

    if site_key in SITES:
        site = SITES[site_key]
    elif site_key.startswith("http"):
        site = {"url": site_key, "success_patterns": None, "cookies": [], "prefix": "custom"}
    else:
        print(f"Unknown site: {site_key}")
        print(f"Available: {', '.join(SITES.keys())} or a full URL")
        sys.exit(1)

    print(f"Opening {site['url']} in headed browser...")
    print(f"Log in via the noVNC web interface (http://localhost:{os.environ.get('NOVNC_PORT', 6080)}/vnc.html)")
    print(f"Waiting up to 10 minutes for login to complete...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-gpu", "--start-maximized"],
        )
        context = await browser.new_context(
            viewport={"width": int(os.environ.get("SCREEN_WIDTH", 1280)),
                       "height": int(os.environ.get("SCREEN_HEIGHT", 800))},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"),
        )
        page = await context.new_page()
        await page.goto(site["url"], wait_until="domcontentloaded")

        # Wait for success URL (up to 10 minutes)
        if site["success_patterns"]:
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
                print(f"Login detected: {page.url}")
            else:
                print(f"Timeout. Current URL: {page.url}")
                print("Checking cookies anyway...")

        await asyncio.sleep(3)

        # Extract ALL cookies
        cookies = await context.cookies()
        all_cookies = {c["name"]: c["value"] for c in cookies}

        # Save all cookies as JSON
        all_file = COOKIE_DIR / f"{site['prefix']}_all_cookies.json"
        all_file.write_text(json.dumps(all_cookies, indent=2))
        print(f"Saved all cookies to {all_file}")

        # Save specific target cookies
        found = {}
        for name in site["cookies"]:
            if name in all_cookies:
                val = all_cookies[name]
                found[name] = val
                cookie_file = COOKIE_DIR / f"{site['prefix']}_{name}.txt"
                cookie_file.write_text(val)
                print(f"  Found {name} ({len(val)} chars) -> {cookie_file}")
            else:
                print(f"  {name} not found")

        # Save Playwright storage state (full browser state)
        state_file = COOKIE_DIR / f"{site['prefix']}_storage_state.json"
        await context.storage_state(path=str(state_file))
        print(f"Storage state saved -> {state_file}")

        if found:
            print(f"\n=== SUCCESS: {len(found)}/{len(site['cookies'])} target cookies extracted ===")
        else:
            print(f"\n=== No target cookies found ===")
            print(f"Available cookies: {', '.join(all_cookies.keys())}")

        # Print summary (no values for security)
        print(f"\nAll cookies for {site['prefix']}:")
        for name, val in all_cookies.items():
            print(f"  {name}: {len(val)} chars")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
