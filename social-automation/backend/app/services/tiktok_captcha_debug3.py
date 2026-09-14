"""Debug what happens after the captcha is 'solved'."""

import asyncio
import json
import logging
import random
import sys

sys.path.insert(0, "/app/app/services")
from tiktok_captcha import _generate_human_trajectory, decrypt_edata

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

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

        # Intercept ALL network responses to track captcha verification
        all_responses = []
        async def handle_response(response):
            if "captcha" in response.url or "verify" in response.url or "profile" in response.url:
                try:
                    body = await response.text()
                    all_responses.append({
                        "url": response.url[:120],
                        "status": response.status,
                        "body": body[:500],
                    })
                except Exception:
                    pass
        page.on("response", handle_response)

        await page.goto(PROFILE_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # Click Edit profile
        await page.get_by_role("button", name="Edit profile").click()
        await asyncio.sleep(2)

        # Fill bio
        await page.get_by_role("textbox", name="Bio").fill(TARGET_BIO)
        await asyncio.sleep(0.5)

        # Click Save
        await page.get_by_role("button", name="Save").click()
        await asyncio.sleep(5)

        # Check captcha state
        state = await page.evaluate("""() => {
            const container = document.querySelector('.captcha-verify-container');
            const dialogs = document.querySelectorAll('[role="dialog"]');
            const editDialog = Array.from(dialogs).find(d => d.textContent.includes('Edit profile') && d.textContent.includes('Bio'));
            return {
                captchaContainer: container ? container.className : 'none',
                captchaVisible: container ? window.getComputedStyle(container).display !== 'none' : false,
                editDialogExists: !!editDialog,
                editDialogText: editDialog ? editDialog.textContent.substring(0, 200) : 'none',
                numDialogs: dialogs.length,
                allDialogClasses: Array.from(dialogs).map(d => d.className.substring(0, 80)),
                bodyText: document.body.innerText.substring(0, 500),
            };
        }""")
        print("=== STATE AFTER SAVE CLICK ===")
        print(json.dumps(state, indent=2))

        # If captcha is present, try to solve it
        if state["captchaVisible"]:
            # Get the captcha info
            captcha_info = await page.evaluate("""() => {
                const container = document.querySelector('.captcha-verify-container');
                if (!container) return null;
                const imgs = container.querySelectorAll('img[alt="Captcha"]');
                if (imgs.length < 2) return null;
                const bg = imgs[0];
                const piece = imgs[1];
                const bgRect = bg.getBoundingClientRect();
                const pieceRect = piece.getBoundingClientRect();
                const slider = container.querySelector('#captcha_slide_button');
                const sliderRect = slider ? slider.getBoundingClientRect() : null;
                if (slider) {
                    slider.classList.remove('TUXButton--disabled');
                    slider.removeAttribute('aria-disabled');
                    slider.disabled = false;
                }
                return {
                    bg: {x: bgRect.x, y: bgRect.y, w: bgRect.width, h: bgRect.height, naturalWidth: bg.naturalWidth},
                    piece: {x: pieceRect.x, y: pieceRect.y, w: pieceRect.width, h: pieceRect.height},
                    slider: sliderRect ? {x: sliderRect.x, y: sliderRect.y, w: sliderRect.width, h: sliderRect.height} : null,
                };
            }""")

            # Get the latest captcha edata from intercepted responses
            captcha_edata = None
            for resp in all_responses:
                if "captcha" in resp["url"] and "get" in resp["url"]:
                    try:
                        data = json.loads(resp["body"])
                        if "edata" in data:
                            captcha_edata = data["edata"]
                        elif "data" in data and isinstance(data["data"], dict) and "edata" in data["data"]:
                            captcha_edata = data["data"]["edata"]
                    except Exception:
                        pass

            if captcha_edata:
                decrypted = decrypt_edata(captcha_edata)
                captcha_data = json.loads(decrypted)
                cyfreso = captcha_data.get("data", {}).get("cyfreso")
                print("\n=== CAPTCHA DATA ===")
                print(f"cyfreso: {cyfreso}")
                print(f"mode: {captcha_data.get('data', {}).get('challenges', [{}])[0].get('mode')}")

                if cyfreso is not None and captcha_info:
                    # Use cyfreso as display gap position
                    gap_x_display = float(cyfreso)
                    piece_start = captcha_info["piece"]["x"] - captcha_info["bg"]["x"]
                    drag_distance = gap_x_display - piece_start
                    print(f"gap_x_display={gap_x_display}, piece_start={piece_start}, drag={drag_distance}")

                    # Drag the slider
                    start_x = captcha_info["slider"]["x"] + captcha_info["slider"]["w"] / 2
                    start_y = captcha_info["slider"]["y"] + captcha_info["slider"]["h"] / 2
                    end_x = start_x + drag_distance
                    end_y = start_y + random.uniform(-2, 2)

                    print(f"Dragging from ({start_x}, {start_y}) to ({end_x}, {end_y})")

                    trajectory = _generate_human_trajectory(start_x, start_y, end_x, end_y)
                    await page.mouse.move(start_x, start_y)
                    await asyncio.sleep(0.3)
                    await page.mouse.down()
                    await asyncio.sleep(0.2)
                    for x, y, delay in trajectory:
                        await page.mouse.move(x, y)
                        await asyncio.sleep(delay / 1000.0)
                    await asyncio.sleep(0.3)
                    await page.mouse.up()
                    await asyncio.sleep(5)

                    # Check state after drag
                    state_after = await page.evaluate("""() => {
                        const container = document.querySelector('.captcha-verify-container');
                        const dialogs = document.querySelectorAll('[role="dialog"]');
                        const editDialog = Array.from(dialogs).find(d => d.textContent.includes('Edit profile') && d.textContent.includes('Bio'));
                        return {
                            captchaContainer: container ? container.className : 'none',
                            captchaVisible: container ? window.getComputedStyle(container).display !== 'none' : false,
                            captchaDisplay: container ? window.getComputedStyle(container).display : 'none',
                            editDialogExists: !!editDialog,
                            numDialogs: dialogs.length,
                            allDialogClasses: Array.from(dialogs).map(d => d.className.substring(0, 80)),
                            bodyText: document.body.innerText.substring(0, 500),
                        };
                    }""")
                    print("\n=== STATE AFTER DRAG ===")
                    print(json.dumps(state_after, indent=2))

                    # Take screenshot
                    await page.screenshot(path="/tmp/captcha-after-drag.png")
                    print("Screenshot saved to /tmp/captcha-after-drag.png")

                    # Wait more and check again
                    await asyncio.sleep(5)
                    state_final = await page.evaluate("""() => {
                        const container = document.querySelector('.captcha-verify-container');
                        const dialogs = document.querySelectorAll('[role="dialog"]');
                        const editDialog = Array.from(dialogs).find(d => d.textContent.includes('Edit profile') && d.textContent.includes('Bio'));
                        return {
                            captchaVisible: container ? window.getComputedStyle(container).display !== 'none' : false,
                            editDialogExists: !!editDialog,
                            bodyText: document.body.innerText.substring(0, 500),
                        };
                    }""")
                    print("\n=== STATE AFTER WAIT ===")
                    print(json.dumps(state_final, indent=2))

        # Print all network responses
        print("\n=== NETWORK RESPONSES ===")
        for resp in all_responses:
            print(f"  [{resp['status']}] {resp['url']}")
            if resp['body']:
                print(f"    Body: {resp['body'][:200]}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
