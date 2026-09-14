"""Standalone TikTok bio update script with captcha solving.

Uses Playwright's native mouse API (CDP-based, isTrusted=true) to solve
TikTok's slider captcha and update the profile bio.

Usage:
    python tiktok_bio_update.py
"""

import asyncio
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# TikTok session cookies (extracted from the authenticated browser)
COOKIES = [
    {"name": "sessionid", "value": "74d3a903473419df1fd5242233be73a6", "domain": ".tiktok.com", "path": "/"},
    {"name": "sessionid_ss", "value": "74d3a903473419df1fd5242233be73a6", "domain": ".tiktok.com", "path": "/"},
    {"name": "passport_csrf_token", "value": "a8e7c9792736b29c18cca0cab49426a4", "domain": ".tiktok.com", "path": "/"},
    {"name": "passport_csrf_token_default", "value": "a8e7c9792736b29c18cca0cab49426a4", "domain": ".tiktok.com", "path": "/"},
    {"name": "msToken", "value": "RUKaZ6CkenRtl8rZuFpuwpOgDPK2XER_Fg9vj9SBOEhkZcr-mJYMaTiDK7qPseiUpq3mq9bxCNsZL4ucZNC1qmS16hBdisEPDJyrG-gxx8HToR1zb1BrRDc1hKFtuRZ73nMIRcN5ZTKS8OUj2nd4apw32DyiG8PGmTl5R-GRn5g=", "domain": ".tiktok.com", "path": "/"},
]

TARGET_BIO = "☁️ Serverless Cloud · AI Marketing\n📍 Athens, GR\n🔗 cloudless.gr"
PROFILE_URL = "https://www.tiktok.com/@user3113682023385?lang=en"

# Import the captcha solver
sys.path.insert(0, "/app/app/services")
from tiktok_captcha import solve_captcha_and_save


async def main():
    from playwright.async_api import async_playwright

    browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/tmp/pw-browsers")

    async with async_playwright() as p:
        # Launch headless Chromium
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )

        # Inject session cookies
        await context.add_cookies(COOKIES)

        page = await context.new_page()

        # Navigate to the TikTok profile page
        logger.info("Navigating to TikTok profile...")
        await page.goto(PROFILE_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # Check if we're logged in
        edit_btn = page.get_by_role("button", name="Edit profile")
        try:
            await edit_btn.wait_for(state="visible", timeout=10000)
            logger.info("Logged in successfully — Edit profile button found")
        except Exception:
            logger.error("Not logged in or Edit profile button not found")
            # Take a screenshot for debugging
            await page.screenshot(path="/tmp/tiktok-login-check.png")
            await browser.close()
            return False

        # Use the captcha solver to update the bio
        logger.info("Starting bio update with captcha solver...")
        success = await solve_captcha_and_save(page, TARGET_BIO)

        if success:
            logger.info("Bio update SUCCESSFUL")

            # Verify the bio was updated
            await asyncio.sleep(3)
            await page.reload(wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)

            bio_heading = await page.evaluate(
                """() => {
                    const headings = document.querySelectorAll('h2');
                    for (const h of headings) {
                        if (h.textContent.includes('Serverless') || h.textContent.includes('Cloud')) {
                            return h.textContent;
                        }
                    }
                    return 'not found';
                }"""
            )
            logger.info("Current bio heading: %s", bio_heading)
        else:
            logger.error("Bio update FAILED")
            await page.screenshot(path="/tmp/tiktok-bio-failed.png")

        await browser.close()
        return success


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
