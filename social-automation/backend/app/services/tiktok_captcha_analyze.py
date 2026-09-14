"""Extract and analyze TikTok captcha images in one run."""

import asyncio
import base64

import cv2
import numpy as np

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

        # Extract captcha images
        captcha_data = await page.evaluate("""() => {
            const container = document.querySelector('.captcha-verify-container');
            if (!container) return null;
            const imgs = container.querySelectorAll('img[alt="Captcha"]');
            if (imgs.length < 2) return null;
            return {bg: imgs[0].src, piece: imgs[1].src};
        }""")

        if not captcha_data:
            print("No captcha found")
            await browser.close()
            return

        # Decode images
        bg_b64 = captcha_data["bg"].split(",")[1]
        piece_b64 = captcha_data["piece"].split(",")[1]

        bg_data = base64.b64decode(bg_b64)
        piece_data = base64.b64decode(piece_b64)

        bg_arr = np.frombuffer(bg_data, dtype=np.uint8)
        piece_arr = np.frombuffer(piece_data, dtype=np.uint8)

        bg = cv2.imdecode(bg_arr, cv2.IMREAD_UNCHANGED)
        piece = cv2.imdecode(piece_arr, cv2.IMREAD_UNCHANGED)

        print(f"BG: shape={bg.shape}, dtype={bg.dtype}")
        print(f"Piece: shape={piece.shape}, dtype={piece.dtype}")

        # Save images
        cv2.imwrite("/tmp/captcha-bg.png", bg)
        cv2.imwrite("/tmp/captcha-piece.png", piece)
        print("Saved /tmp/captcha-bg.png and /tmp/captcha-piece.png")

        # Convert to grayscale
        bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY) if len(bg.shape) == 3 else bg
        piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY) if len(piece.shape) == 3 else piece

        # Method 1: Find the darkest column (gap is typically darker)
        col_means = np.mean(bg_gray, axis=0)
        darkest_col = np.argmin(col_means)
        print(f"\nDarkest column: {darkest_col} (mean={col_means[darkest_col]:.1f})")

        # Method 2: Column variance
        col_vars = np.var(bg_gray, axis=0)
        highest_var_col = np.argmax(col_vars)
        print(f"Highest variance column: {highest_var_col} (var={col_vars[highest_var_col]:.1f})")

        # Method 3: Edge detection
        edges = cv2.Canny(bg_gray, 50, 150)
        edge_cols = np.sum(edges, axis=0)
        # Look in the right half
        half = len(edge_cols) // 3  # Gap is usually in the right 2/3
        right_part = edge_cols[half:]
        gap_edge = half + np.argmax(right_part)
        print(f"Gap (edge detection): {gap_edge} (edges={edge_cols[gap_edge]})")

        # Method 4: Template matching at different scales
        piece_edges = cv2.Canny(piece_gray, 50, 150)
        print("\nTemplate matching at different scales:")
        for scale in [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]:
            sw = int(piece_edges.shape[1] * scale)
            sh = int(piece_edges.shape[0] * scale)
            if sw < 10 or sh < 10:
                continue
            resized = cv2.resize(piece_edges, (sw, sh))
            if sh < bg_gray.shape[0] and sw < bg_gray.shape[1]:
                matched = cv2.matchTemplate(edges, resized, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(matched)
                print(f"  scale {scale}: pos=({max_loc[0]}, {max_loc[1]}), conf={max_val:.3f}, size={sw}x{sh}")

        # Method 5: Look for the gap using difference from mean
        # The gap region has a different texture/brightness
        row_mean = np.mean(bg_gray, axis=1, keepdims=True)
        diff = np.abs(bg_gray.astype(float) - row_mean)
        col_diff = np.mean(diff, axis=0)
        gap_diff = np.argmax(col_diff)
        print(f"\nGap (diff from row mean): {gap_diff} (diff={col_diff[gap_diff]:.1f})")

        # Print column stats every 20px
        print("\nColumn stats (every 20px):")
        for i in range(0, bg_gray.shape[1], 20):
            print(f"  col {i}: mean={col_means[i]:.1f}, var={col_vars[i]:.1f}, edges={edge_cols[i]}, diff={col_diff[i]:.1f}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
