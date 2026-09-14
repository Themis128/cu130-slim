"""TikTok slider captcha solver — API-based gap detection + browser drag.

Intercepts the TikTok captcha API response, decrypts the edata to get
the captcha images, uses OpenCV Sobel+Canny template matching to find
the gap position, then drags the slider using Playwright's native mouse
API (CDP-based, isTrusted=true).

Based on the open-source Gisnsl/tiktok-captcha-solver approach.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import secrets

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ─── ChaCha encryption for edata decryption ────────────────────────────

class _Cha:
    """ChaCha-like encryption used by TikTok's edata format."""

    def __init__(self, key: bytes, nonce: bytes, counter: int = 0):
        if len(key) != 32 or len(nonce) != 12:
            raise ValueError("Invalid key or nonce length")
        self.k = key
        self.n = nonce
        self.c = counter

    @staticmethod
    def _r(v, n):
        return ((v << n) & 0xFFFFFFFF) | (v >> (32 - n))

    @staticmethod
    def _qr(s, a, b, c, d):
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF
        s[d] ^= s[a]
        s[d] = _Cha._r(s[d], 16)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF
        s[b] ^= s[c]
        s[b] = _Cha._r(s[b], 12)
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF
        s[d] ^= s[a]
        s[d] = _Cha._r(s[d], 8)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF
        s[b] ^= s[c]
        s[b] = _Cha._r(s[b], 7)

    def _block(self, ctr):
        s = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]
        s += [int.from_bytes(self.k[i*4:(i+1)*4], "little") for i in range(8)]
        s.append(ctr & 0xFFFFFFFF)
        s += [int.from_bytes(self.n[i*4:(i+1)*4], "little") for i in range(3)]
        w = s[:]
        for _ in range(10):
            self._qr(w, 0, 4, 8, 12)
            self._qr(w, 1, 5, 9, 13)
            self._qr(w, 2, 6, 10, 14)
            self._qr(w, 3, 7, 11, 15)
            self._qr(w, 0, 5, 10, 15)
            self._qr(w, 1, 6, 11, 12)
            self._qr(w, 2, 7, 8, 13)
            self._qr(w, 3, 4, 9, 14)
        return b''.join(((w[i]+s[i]) & 0xFFFFFFFF).to_bytes(4, "little") for i in range(16))

    def _ks(self):
        ctr = self.c
        while True:
            block = self._block(ctr)
            ctr = (ctr + 1) & 0xFFFFFFFF
            for b in block:
                yield b

    def _p(self, data):
        ks = self._ks()
        return bytes([b ^ next(ks) for b in data])


def decrypt_edata(txt: str) -> str:
    """Decrypt TikTok's edata (ChaCha-encrypted base64)."""
    padding = 4 - len(txt) % 4
    if padding != 4:
        txt += "=" * padding
    raw = base64.b64decode(txt)
    if len(raw) < 1 + 32 + 12:
        raise ValueError("Invalid edata")
    k = raw[1:33]
    n = raw[33:45]
    ct = raw[45:]
    c = _Cha(k, n, 0)
    return c._p(ct).decode("utf-8", errors="replace")


def encrypt_edata(txt: str) -> str:
    """Encrypt data in TikTok's edata format."""
    d = txt.encode("utf-8")
    k = secrets.token_bytes(32)
    n = secrets.token_bytes(12)
    c = _Cha(k, n, 0)
    ct = c._p(d)
    raw = b"\x01" + k + n + ct
    return base64.b64encode(raw).decode()


# ─── Puzzle solver (OpenCV Sobel + Canny template matching) ────────────

def _decode_img(b64: str) -> np.ndarray:
    """Decode base64 image to BGR numpy array (matching PuzzleSolver._img)."""
    data = base64.b64decode(b64)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Failed to decode image")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    return img


def _sobel(img: np.ndarray) -> np.ndarray:
    """Sobel edge detection with Gaussian blur (matching PuzzleSolver._sobel)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    ax = cv2.convertScaleAbs(gx)
    ay = cv2.convertScaleAbs(gy)
    grad = cv2.addWeighted(ax, 0.5, ay, 0.5, 0)
    return cv2.normalize(grad, None, 0, 255, cv2.NORM_MINMAX)


def _enhance(img: np.ndarray) -> np.ndarray:
    """CLAHE enhancement (matching PuzzleSolver._enhance)."""
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(img)


def _edges(img: np.ndarray) -> np.ndarray:
    """Canny edges (matching PuzzleSolver._edges)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.Canny(blurred, 50, 150)


def find_gap_from_api_images(bg_b64: str, piece_b64: str) -> int:
    """Find the gap position using combined template matching + column analysis.

    Template matching finds the piece's starting position. Column variance
    analysis finds regions with structural differences (the gap). The gap
    is the region with high variance that is NOT at the starting position.

    Args:
        bg_b64: Base64-encoded background image (with the gap).
        piece_b64: Base64-encoded puzzle piece image.

    Returns:
        The X pixel position of the gap in the background image (natural size).
    """
    bg = _decode_img(bg_b64)
    piece = _decode_img(piece_b64)

    logger.info("TikTok captcha: bg shape=%s, piece shape=%s", bg.shape, piece.shape)

    # Step 1: Template matching to find the piece's starting position
    methods = (cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR_NORMED)
    tm_results = []

    p_sobel = _sobel(piece)
    t_sobel = _sobel(bg)
    for m in methods:
        matched = cv2.matchTemplate(t_sobel, p_sobel, m)
        mn, mx, mn_loc, mx_loc = cv2.minMaxLoc(matched)
        tm_results.append((mx_loc[0], mx))

    p_enh = _enhance(p_sobel)
    t_enh = _enhance(t_sobel)
    for m in methods:
        matched = cv2.matchTemplate(t_enh, p_enh, m)
        mn, mx, mn_loc, mx_loc = cv2.minMaxLoc(matched)
        tm_results.append((mx_loc[0], mx))

    p_edges = _edges(piece)
    t_edges = _edges(bg)
    matched = cv2.matchTemplate(t_edges, p_edges, cv2.TM_CCOEFF_NORMED)
    mn, mx, mn_loc, mx_loc = cv2.minMaxLoc(matched)
    tm_results.append((mx_loc[0], mx))

    tm_results.sort(key=lambda x: x[1], reverse=True)
    start_pos = tm_results[0][0]

    logger.info(
        "TikTok captcha: template matching start_pos=%d (conf=%.3f, all: %s)",
        start_pos, tm_results[0][1], [(p, round(c, 3)) for p, c in tm_results[:5]],
    )

    # Step 2: Column variance analysis to find the gap
    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY) if bg.ndim == 3 else bg
    col_vars = np.var(bg_gray.astype(float), axis=0)

    # Step 3: Edge density analysis
    edges_img = cv2.Canny(bg_gray, 50, 150)
    edge_cols = np.sum(edges_img, axis=0)

    # Step 4: Find the gap — the region with high variance/edges that is
    # NOT at the starting position. Exclude a window around the start.
    exclude_window = piece.shape[1]  # Exclude the piece width around start
    col_vars_masked = col_vars.copy()
    edge_cols_masked = edge_cols.copy().astype(float)

    # Exclude the starting position region
    x_min = max(0, start_pos - exclude_window // 2)
    x_max = min(len(col_vars), start_pos + exclude_window // 2)
    col_vars_masked[x_min:x_max] = 0
    edge_cols_masked[x_min:x_max] = 0

    # Also exclude the far left (the piece never starts at x=0)
    col_vars_masked[:20] = 0
    edge_cols_masked[:20] = 0

    gap_var = int(np.argmax(col_vars_masked))
    gap_edge = int(np.argmax(edge_cols_masked))

    # Use the average of the two methods if they're close, otherwise pick
    # the one with the higher relative peak
    if abs(gap_var - gap_edge) < 30:
        gap_pos = (gap_var + gap_edge) // 2
    else:
        # Pick the one with the higher peak relative to the median
        var_peak = col_vars_masked[gap_var] / (np.median(col_vars_masked[col_vars_masked > 0]) + 1)
        edge_peak = edge_cols_masked[gap_edge] / (np.median(edge_cols_masked[edge_cols_masked > 0]) + 1)
        gap_pos = gap_var if var_peak > edge_peak else gap_edge

    logger.info(
        "TikTok captcha: gap_var=%d, gap_edge=%d, selected=%d (start_pos=%d excluded)",
        gap_var, gap_edge, gap_pos, start_pos,
    )

    return gap_pos


# ─── Human-like mouse trajectory ──────────────────────────────────────

def _generate_human_trajectory(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    num_steps: int = 50,
) -> list[tuple[float, float, float]]:
    """Generate a human-like mouse trajectory from start to end."""
    points = []
    ctrl_x = start_x + (end_x - start_x) * 0.5 + random.uniform(-20, 20)
    ctrl_y = start_y + random.uniform(-5, 5)

    for i in range(num_steps + 1):
        t = i / num_steps
        x = (1 - t)**2 * start_x + 2*(1-t)*t * ctrl_x + t**2 * end_x
        y = (1 - t)**2 * start_y + 2*(1-t)*t * ctrl_y + t**2 * end_y
        x += random.uniform(-1, 1)
        y += random.uniform(-1, 1)
        if i < num_steps * 0.7:
            delay = random.uniform(5, 15)
        else:
            delay = random.uniform(10, 30)
        points.append((x, y, delay))

    points[-1] = (end_x, end_y, 20)
    return points


# ─── Main captcha solver ──────────────────────────────────────────────

async def solve_captcha_and_save(page, bio: str) -> bool:
    """Complete flow: fill bio, click Save, solve captcha, verify save.

    Intercepts the captcha API response, decrypts the edata to get the
    images, uses OpenCV to find the gap position, then drags the slider
    using Playwright's native mouse API.

    Args:
        page: Playwright Page object (already on the TikTok profile page).
        bio: The new bio text to set.

    Returns:
        True if the bio was saved successfully, False otherwise.
    """
    # Set up response interceptor for the captcha API
    captcha_edata_list = []

    async def handle_response(response):
        if "captcha" in response.url and "/get" in response.url:
            try:
                body = await response.text()
                data = json.loads(body)
                if "edata" in data:
                    captcha_edata_list.append(data["edata"])
                elif "data" in data and isinstance(data["data"], dict) and "edata" in data["data"]:
                    captcha_edata_list.append(data["data"]["edata"])
            except Exception:
                pass

    page.on("response", handle_response)

    # Click "Edit profile" button
    edit_btn = page.get_by_role("button", name="Edit profile")
    await edit_btn.click()
    await asyncio.sleep(2)

    # Fill the Bio field
    bio_field = page.get_by_role("textbox", name="Bio")
    await bio_field.fill(bio)
    await asyncio.sleep(0.5)

    # Click Save
    save_btn = page.get_by_role("button", name="Save")
    await save_btn.click()
    await asyncio.sleep(5)

    # Check if a captcha appeared
    captcha_present = await page.evaluate(
        """() => {
            const container = document.querySelector('.captcha-verify-container');
            if (!container) return false;
            const style = window.getComputedStyle(container);
            return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
        }"""
    )

    if not captcha_present:
        logger.info("TikTok captcha: did not appear — save may have succeeded directly")
        edit_open = await page.evaluate(
            """() => {
                const dialogs = document.querySelectorAll('[role="dialog"]');
                for (const d of dialogs) {
                    if (d.textContent.includes('Edit profile') && d.textContent.includes('Bio')) {
                        return true;
                    }
                }
                return false;
            }"""
        )
        return not edit_open

    logger.info("TikTok captcha: appeared, solving...")

    # Decrypt the captcha API response to get the images
    if not captcha_edata_list:
        logger.error("TikTok captcha: no API response intercepted")
        return False

    try:
        decrypted = decrypt_edata(captcha_edata_list[-1])
        captcha_data = json.loads(decrypted)
    except Exception as e:
        logger.error("TikTok captcha: failed to decrypt edata: %s", e)
        return False

    # Extract the challenge data
    challenges = captcha_data.get("data", {}).get("challenges", [])
    slide_challenge = None
    for ch in challenges:
        if ch.get("mode") in ("whirl", "slide"):
            slide_challenge = ch
            break

    if not slide_challenge:
        logger.error("TikTok captcha: no slide/whirl challenge found")
        return False

    question = slide_challenge.get("question", {})
    object1 = question.get("object1")
    object2 = question.get("object2")
    objects = question.get("objects", {})

    if not object1 or not object2 or object1 not in objects or object2 not in objects:
        logger.error("TikTok captcha: missing image objects")
        return False

    bg_b64 = objects[object1]
    piece_b64 = objects[object2]

    # Find the gap position using combined template matching + column analysis
    # The cyfreso field from the API is NOT the gap position — it's an
    # internal SDK state value. We use image analysis instead.
    gap_x = find_gap_from_api_images(bg_b64, piece_b64)

    # Get the browser display info for the captcha
    captcha_info = await page.evaluate("""() => {
        const container = document.querySelector('.captcha-verify-container');
        if (!container) return {error: 'no container'};
        const imgs = container.querySelectorAll('img[alt="Captcha"]');
        if (imgs.length < 2) return {error: 'not enough images'};
        const bg = imgs[0];
        const piece = imgs[1];
        const bgRect = bg.getBoundingClientRect();
        const pieceRect = piece.getBoundingClientRect();
        const slider = container.querySelector('#captcha_slide_button');
        const sliderRect = slider ? slider.getBoundingClientRect() : null;
        // Remove disabled class
        if (slider) {
            slider.classList.remove('TUXButton--disabled');
            slider.removeAttribute('aria-disabled');
            slider.disabled = false;
        }
        return {
            bg: {x: bgRect.x, y: bgRect.y, w: bgRect.width, h: bgRect.height,
                 naturalWidth: bg.naturalWidth, naturalHeight: bg.naturalHeight},
            piece: {x: pieceRect.x, y: pieceRect.y, w: pieceRect.width, h: pieceRect.height},
            slider: sliderRect ? {x: sliderRect.x, y: sliderRect.y, w: sliderRect.width, h: sliderRect.height} : null,
        };
    }""")

    if "error" in captcha_info:
        logger.error("TikTok captcha: %s", captcha_info["error"])
        return False

    logger.info("TikTok captcha browser info: %s", json.dumps(captcha_info, indent=2))

    # Decode the API images to get their natural dimensions
    bg_img = _decode_img(bg_b64)
    api_bg_width = bg_img.shape[1]

    # The cyfreso field is NOT the gap position — use image analysis.
    # Scale from API image (natural) to browser display coordinates.
    browser_bg_display_w = captcha_info["bg"]["w"]
    scale = browser_bg_display_w / api_bg_width
    gap_x_display = gap_x * scale
    logger.info(
        "TikTok captcha: gap_x_natural=%d, scale=%.3f, gap_x_display=%.1f",
        gap_x, scale, gap_x_display,
    )

    # The piece's starting position in browser display coordinates
    piece_start_display = captcha_info["piece"]["x"] - captcha_info["bg"]["x"]

    # The drag distance is the gap position minus the piece's starting position
    drag_distance = gap_x_display - piece_start_display

    logger.info(
        "TikTok captcha: gap_x_display=%.1f, piece_start=%.1f, drag=%.1f",
        gap_x_display, piece_start_display, drag_distance,
    )

    if not captcha_info["slider"]:
        logger.error("TikTok captcha: slider button not found")
        return False

    # Slider starting position (center of the slider button)
    start_x = captcha_info["slider"]["x"] + captcha_info["slider"]["w"] / 2
    start_y = captcha_info["slider"]["y"] + captcha_info["slider"]["h"] / 2
    end_x = start_x + drag_distance
    end_y = start_y + random.uniform(-2, 2)

    logger.info(
        "TikTok captcha: dragging slider from (%.1f, %.1f) to (%.1f, %.1f), drag=%.1f",
        start_x, start_y, end_x, end_y, drag_distance,
    )

    # Try solving up to 3 times
    for attempt in range(3):
        logger.info("TikTok captcha: attempt %d", attempt + 1)

        # Generate human-like trajectory
        trajectory = _generate_human_trajectory(start_x, start_y, end_x, end_y)

        # Move to the slider first (hover)
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(random.uniform(0.2, 0.5))

        # Press down on the slider
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.1, 0.3))

        # Drag along the trajectory
        for x, y, delay in trajectory:
            await page.mouse.move(x, y)
            await asyncio.sleep(delay / 1000.0)

        # Pause at the end (humans hesitate)
        await asyncio.sleep(random.uniform(0.2, 0.5))

        # Release
        await page.mouse.up()

        # Wait for verification
        await asyncio.sleep(3)

        # Check if the captcha was solved
        captcha_solved = await page.evaluate(
            """() => {
                const container = document.querySelector('.captcha-verify-container');
                if (!container) return true;
                const style = window.getComputedStyle(container);
                return style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0';
            }"""
        )

        if captcha_solved:
            logger.info("TikTok captcha: SOLVED")
            # Wait for the save to complete after captcha
            await asyncio.sleep(3)

            # Check if the edit dialog is gone (save succeeded)
            edit_open = await page.evaluate(
                """() => {
                    const dialogs = document.querySelectorAll('[role="dialog"]');
                    for (const d of dialogs) {
                        if (d.textContent.includes('Edit profile') && d.textContent.includes('Bio')) {
                            return true;
                        }
                    }
                    return false;
                }"""
            )
            return not edit_open

        logger.warning("TikTok captcha: NOT solved (attempt %d)", attempt + 1)

        if attempt < 2:
            # Refresh the captcha via JavaScript
            await page.evaluate("""() => {
                const container = document.querySelector('.captcha-verify-container');
                if (container) {
                    const btns = container.querySelectorAll('button, [role="button"]');
                    for (const b of btns) {
                        if (b.id !== 'captcha_slide_button' && b.offsetParent !== null) {
                            const rect = b.getBoundingClientRect();
                            if (rect.width < 50) {
                                b.click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            }""")
            await asyncio.sleep(3)

            # Re-intercept the new captcha API response
            if captcha_edata_list:
                try:
                    new_decrypted = decrypt_edata(captcha_edata_list[-1])
                    new_data = json.loads(new_decrypted)
                    new_challenges = new_data.get("data", {}).get("challenges", [])
                    for ch in new_challenges:
                        if ch.get("mode") in ("whirl", "slide"):
                            new_q = ch.get("question", {})
                            new_o1 = new_q.get("object1")
                            new_o2 = new_q.get("object2")
                            new_objs = new_q.get("objects", {})
                            if new_o1 in new_objs and new_o2 in new_objs:
                                gap_x = find_gap_from_api_images(new_objs[new_o1], new_objs[new_o2])
                                gap_x_display = gap_x * scale
                                drag_distance = gap_x_display - piece_start_display
                                end_x = start_x + drag_distance
                                logger.info(
                                    "TikTok captcha: new gap_x=%d, drag=%.1f",
                                    gap_x, drag_distance,
                                )
                            break
                except Exception as e:
                    logger.warning("TikTok captcha: failed to re-decrypt: %s", e)

    logger.error("TikTok captcha: failed to solve after 3 attempts")
    return False
