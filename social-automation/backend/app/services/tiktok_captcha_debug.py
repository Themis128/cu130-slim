"""Debug TikTok captcha — extract images and analyze dimensions."""

import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, "/app/app/services")

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

        # Debug the captcha
        debug_info = await page.evaluate("""() => {
            const dialogs = document.querySelectorAll('[role="dialog"]');
            let captchaDialog = null;
            for (const d of dialogs) {
                if (d.textContent.includes('puzzle') || d.textContent.includes('Drag') || d.querySelector('img')) {
                    const imgs = d.querySelectorAll('img');
                    if (imgs.length >= 2) {
                        captchaDialog = d;
                        break;
                    }
                }
            }
            if (!captchaDialog) return JSON.stringify({error: 'no captcha dialog found', numDialogs: dialogs.length});

            const imgs = captchaDialog.querySelectorAll('img');
            const slider = captchaDialog.querySelector('.secsdk-captcha-drag-icon') || captchaDialog.querySelector('[class*="drag"]') || captchaDialog.querySelector('[class*="slider"]');
            const allElements = captchaDialog.querySelectorAll('*');

            // Get all image info
            const imgInfo = [];
            imgs.forEach((img, i) => {
                const rect = img.getBoundingClientRect();
                imgInfo.push({
                    index: i,
                    src: img.src.substring(0, 80),
                    naturalWidth: img.naturalWidth,
                    naturalHeight: img.naturalHeight,
                    displayWidth: rect.width,
                    displayHeight: rect.height,
                    displayLeft: rect.left,
                    displayTop: rect.top,
                    alt: img.alt,
                    className: img.className.substring(0, 80),
                });
            });

            // Get slider info
            let sliderInfo = null;
            if (slider) {
                const rect = slider.getBoundingClientRect();
                sliderInfo = {
                    x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                    className: slider.className.substring(0, 100),
                    disabled: slider.classList.contains('TUXButton--disabled'),
                };
            }

            // Get all draggable elements
            const dragElements = [];
            captchaDialog.querySelectorAll('[class*="drag"], [class*="slider"], [class*="captcha"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    dragElements.push({
                        tag: el.tagName,
                        className: el.className.substring(0, 80),
                        x: rect.x, y: rect.y, w: rect.width, h: rect.height,
                    });
                }
            });

            // Get captcha container dimensions
            const container = captchaDialog.querySelector('[class*="captcha_container"]') || captchaDialog.querySelector('[class*="captcha"]');
            let containerInfo = null;
            if (container) {
                const rect = container.getBoundingClientRect();
                containerInfo = {x: rect.x, y: rect.y, w: rect.width, h: rect.height, className: container.className.substring(0, 80)};
            }

            return JSON.stringify({
                numImages: imgs.length,
                images: imgInfo,
                slider: sliderInfo,
                dragElements: dragElements.slice(0, 10),
                container: containerInfo,
                dialogText: captchaDialog.textContent.substring(0, 200),
            });
        }""")

        print("=== CAPTCHA DEBUG INFO ===")
        info = json.loads(debug_info)
        print(json.dumps(info, indent=2))

        # Save the captcha images for analysis
        img_data = await page.evaluate("""() => {
            const dialogs = document.querySelectorAll('[role="dialog"]');
            for (const d of dialogs) {
                const imgs = d.querySelectorAll('img');
                if (imgs.length >= 2) {
                    return {bg: imgs[0].src, piece: imgs[1].src};
                }
            }
            return null;
        }""")

        if img_data:
            # Save images to /tmp
            for name, src in [("bg", img_data["bg"]), ("piece", img_data["piece"])]:
                if src.startswith("data:"):
                    b64 = src.split(",")[1]
                else:
                    b64 = src
                with open(f"/tmp/captcha-{name}.b64", "w") as f:
                    f.write(b64)
                print(f"Saved /tmp/captcha-{name}.b64 (length: {len(b64)})")

        await page.screenshot(path="/tmp/captcha-debug.png", full_page=True)
        print("Screenshot saved to /tmp/captcha-debug.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
