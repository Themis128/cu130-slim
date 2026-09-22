"""Extract gap position from TikTok captcha API response.

The TikTok captcha API (verification16-normal-*.tiktokw.eu/captcha/get)
returns JSON with the gap position encoded in the 'edata' field.
We intercept this response to get the exact gap position.
"""

import asyncio
import sys

sys.path.insert(0, "/app/app/services")

COOKIES = [
    {"name": "sessionid", "value": "74d3a903473419df1fd5242233be73a6", "domain": ".tiktok.com", "path": "/"},
    {"name": "sessionid_ss", "value": "74d3a903473419df1fd5242233be73a6", "domain": ".tiktok.com", "path": "/"},
    {"name": "passport_csrf_token", "value": "a8e7c9792736b29c18cca0cab49426a4", "domain": ".tiktok.com", "path": "/"},
    {"name": "passport_csrf_token_default", "value": "a8e7c9792736b29c18cca0cab49426a4", "domain": ".tiktok.com", "path": "/"},
    {"name": "msToken", "value": "RUKaZ6CkenRtl8rZuFpuwpOgDPK2XER_Fg9vj9SBOEhkZcr-mJYMaTiDK7qPseiUpq3mq9bxCNsZL4ucZNC1qmS16hBdisEPDJyrG-gxx8HToR1zb1BrRDc1hKFtuRZ73nMIRcN5ZTKS8OUj2nd4apw32DyiG8PGmTl5R-GRn5g=", "domain": ".tiktok.com", "path": "/"},  # noqa: E501
]

TARGET_BIO = "☁️ Serverless Cloud · AI Marketing\n📍 Athens, GR\n🔗 cloudless.gr"
PROFILE_URL = "https://www.tiktok.com/@user3113682023385?lang=en"


async def main():
    try:
        from patchright.async_api import async_playwright
    except ImportError:
        from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        await context.add_cookies(COOKIES)
        page = await context.new_page()

        # Intercept captcha API responses
        captcha_responses = []

        async def handle_response(response):
            if "captcha" in response.url and "get" in response.url:
                try:
                    body = await response.text()
                    captcha_responses.append({
                        "url": response.url[:100],
                        "status": response.status,
                        "body": body[:2000],
                    })
                    print(f"CAPTCHA API response: {response.url[:80]}")
                    print(f"  Status: {response.status}")
                    print(f"  Body (first 500): {body[:500]}")
                except Exception as e:
                    print(f"Error reading captcha response: {e}")

        page.on("response", handle_response)

        await page.goto(PROFILE_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # Click Edit profile
        edit_btn = page.get_by_role("button", name="Edit profile")
        await edit_btn.click()
        await asyncio.sleep(2)

        # Fill bio
        bio_field = page.get_by_role("textbox", name="Bio")
        await bio_field.fill(TARGET_BIO)
        await asyncio.sleep(0.5)

        # Click Save
        save_btn = page.get_by_role("button", name="Save")
        await save_btn.click()
        await asyncio.sleep(5)

        # Also intercept the captcha JS to find the gap position
        # TikTok's captcha JS might expose the gap position in a global variable
        gap_info = await page.evaluate("""() => {
            // Check for captcha-related global variables
            const result = {};

            // Check if the captcha SDK exposes any data
            if (window.TTCaptcha) {
                result.TTCaptcha = Object.keys(window.TTCaptcha);
            }

            // Look for captcha data in the DOM
            const container = document.querySelector('.captcha-verify-container');
            if (container) {
                // Check for data attributes
                result.dataAttrs = {};
                for (const attr of container.attributes) {
                    if (attr.name.startsWith('data-')) {
                        result.dataAttrs[attr.name] = attr.value;
                    }
                }

                // Check for hidden script data
                const scripts = container.querySelectorAll('script');
                result.scripts = scripts.length;

                // Check for the captcha instance
                const captchaDiv = container.querySelector('[class*="captcha"]');
                if (captchaDiv) {
                    result.captchaDiv = {
                        className: captchaDiv.className.substring(0, 100),
                        id: captchaDiv.id,
                    };
                }
            }

            // Check for the gap position in the captcha image
            // The puzzle piece position might reveal the gap
            const imgs = container ? container.querySelectorAll('img[alt="Captcha"]') : [];
            if (imgs.length >= 2) {
                const bg = imgs[0];
                const piece = imgs[1];
                const bgRect = bg.getBoundingClientRect();
                const pieceRect = piece.getBoundingClientRect();
                result.imagePositions = {
                    bg: {x: bgRect.x, y: bgRect.y, w: bgRect.width, h: bgRect.height},
                    piece: {x: pieceRect.x, y: pieceRect.y, w: pieceRect.width, h: pieceRect.height},
                    pieceOffset: pieceRect.x - bgRect.x,
                };
            }

            return JSON.stringify(result);
        }""")
        print("\n=== CAPTCHA DOM INFO ===")
        print(gap_info)

        print(f"\n=== CAPTCHA API RESPONSES ({len(captcha_responses)}) ===")
        for resp in captcha_responses:
            print(f"URL: {resp['url']}")
            print(f"Status: {resp['status']}")
            print(f"Body: {resp['body'][:500]}")
            print("---")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
