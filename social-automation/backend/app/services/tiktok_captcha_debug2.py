"""Debug TikTok captcha v2 — find the correct captcha elements."""

import asyncio
import json
import os
import sys

COOKIES = [
    {"name": "sessionid", "value": "74d3a903473419df1fd5242233be73a6", "domain": ".tiktok.com", "path": "/"},
    {"name": "sessionid_ss", "value": "74d3a903473419df1fd5242233be73a6", "domain": ".tiktok.com", "path": "/"},
    {"name": "passport_csrf_token", "value": "a8e7c9792736b29c18cca0cab49426a4", "domain": ".tiktok.com", "path": "/"},
    {"name": "passport_csrf_token_default", "value": "a8e7c9792736b29c18cca0cab49426a4", "domain": ".tiktok.com", "path": "/"},
    {"name": "msToken", "value": "RUKaZ6CkenRtl8rZuFpuwpOgDPK2XER_Fg9vj9SBOEhkZcr-mJYMaTiDK7qPseiUpq3mq9bxCNsZL4ucZNC1qmS16hBdisEPDJyrG-gxx8HToR1zb1BrRDc1hKFtuRZ73nMIRcN5ZTKS8OUj2nd4apw32DyiG8PGmTl5R-GRn5g=", "domain": ".tiktok.com", "path": "/"},
]

TARGET_BIO = "☁️ Serverless Cloud · AI Marketing\n📍 Athens, GR\n🔗 cloudless.gr"
PROFILE_URL = "https://www.tiktok.com/@user3113682023385?lang=en"


async def main():
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

        # Set up a request interceptor to capture captcha-related requests
        captcha_requests = []
        page.on("request", lambda req: captcha_requests.append({"url": req.url[:100], "method": req.method}) if "captcha" in req.url.lower() else None)

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

        # Comprehensive page analysis
        debug_info = await page.evaluate("""() => {
            // Find ALL elements with captcha-related classes or IDs
            const allElements = document.querySelectorAll('*');
            const captchaElements = [];
            const allImages = [];

            for (const el of allElements) {
                const cls = el.className || '';
                const id = el.id || '';
                const tag = el.tagName;

                // Check for captcha-related classes
                if (typeof cls === 'string' && (
                    cls.includes('captcha') || cls.includes('Captcha') ||
                    cls.includes('secsdk') || cls.includes('slider') ||
                    cls.includes('Slider') || cls.includes('drag') ||
                    cls.includes('Drag') || cls.includes('puzzle') ||
                    cls.includes('verify') || cls.includes('Verify')
                )) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 || rect.height > 0) {
                        captchaElements.push({
                            tag, id: id.substring(0, 30),
                            className: cls.substring(0, 100),
                            x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                            visible: rect.width > 0 && rect.height > 0,
                        });
                    }
                }
            }

            // Find ALL images on the page
            for (const img of document.querySelectorAll('img')) {
                const rect = img.getBoundingClientRect();
                if (rect.width > 50 && rect.height > 50) {  // Filter out small icons
                    allImages.push({
                        src: img.src.substring(0, 80),
                        naturalWidth: img.naturalWidth,
                        naturalHeight: img.naturalHeight,
                        displayWidth: rect.width,
                        displayHeight: rect.height,
                        x: rect.x, y: rect.y,
                        alt: img.alt || '',
                        className: (img.className || '').substring(0, 60),
                    });
                }
            }

            // Check for iframes
            const iframes = [];
            for (const iframe of document.querySelectorAll('iframe')) {
                const rect = iframe.getBoundingClientRect();
                iframes.push({
                    src: iframe.src.substring(0, 80),
                    x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                });
            }

            // Check for canvas elements
            const canvases = [];
            for (const canvas of document.querySelectorAll('canvas')) {
                const rect = canvas.getBoundingClientRect();
                canvases.push({
                    x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                });
            }

            return JSON.stringify({
                captchaElements: captchaElements.slice(0, 20),
                largeImages: allImages.slice(0, 10),
                iframes,
                canvases,
                captchaRequests: window.__captchaRequests || [],
            });
        }""")

        print("=== COMPREHENSIVE PAGE ANALYSIS ===")
        info = json.loads(debug_info)
        print(json.dumps(info, indent=2))

        # Also check for the captcha in a different way — look for the specific TikTok captcha container
        captcha_container = await page.evaluate("""() => {
            // TikTok captcha is usually in a div with class containing 'captcha_container' or 'secsdk-captcha'
            const selectors = [
                '.captcha_container',
                '[class*="captcha_container"]',
                '[class*="secsdk-captcha"]',
                '[class*="captcha-verify"]',
                '#secsdk-captcha-drag-wrapper',
                '[class*="secsdk-captcha-drag"]',
            ];

            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el) {
                    const rect = el.getBoundingClientRect();
                    return JSON.stringify({
                        selector: sel,
                        found: true,
                        x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                        html: el.outerHTML.substring(0, 500),
                    });
                }
            }
            return JSON.stringify({found: false, selectorsChecked: selectors});
        }""")
        print("\n=== CAPTCHA CONTAINER SEARCH ===")
        print(captcha_container)

        # Print captured captcha requests
        print("\n=== CAPTCHA-RELATED NETWORK REQUESTS ===")
        for req in captcha_requests:
            print(f"  {req['method']} {req['url']}")

        await page.screenshot(path="/tmp/captcha-debug2.png", full_page=True)
        print("\nScreenshot saved to /tmp/captcha-debug2.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
