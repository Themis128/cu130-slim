"""TikTok slider captcha solver — OpenCV gap detection + human-like mouse drag.

Uses OpenCV template matching to find the puzzle gap position, then
Playwright's native ``page.mouse`` API (CDP-based, ``isTrusted=true``) to
drag the slider with a human-like trajectory.

Key insight: Playwright's ``page.mouse.move()`` uses CDP
``Input.dispatchMouseEvent`` which produces events with ``isTrusted=true``.
Synthetic JavaScript ``MouseEvent`` dispatch produces ``isTrusted=false``
which TikTok's captcha rejects.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import random

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _decode_b64_image(b64: str) -> np.ndarray:
    """Decode a base64-encoded image (webp/png/jpeg) to a grayscale numpy array."""
    data = base64.b64decode(b64)
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("Failed to decode image")
    return img


def _sobel(img: np.ndarray) -> np.ndarray:
    """Apply Sobel edge detection to enhance puzzle piece edges."""
    sx = cv2.Sobel(img, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(img, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(sx, sy)
    mag = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX)
    return mag.astype(np.uint8)


def find_gap_position(bg_b64: str, piece_b64: str) -> int:
    """Find the X position of the puzzle gap in the background image.

    Uses OpenCV template matching with Sobel edge detection for robust
    gap detection across different captcha images.

    Args:
        bg_b64: Base64-encoded background image (with the gap).
        piece_b64: Base64-encoded puzzle piece image.

    Returns:
        The X pixel position of the gap in the background image (natural size).
    """
    bg = _decode_b64_image(bg_b64)
    piece = _decode_b64_image(piece_b64)

    logger.info("TikTok captcha: bg shape=%s, piece shape=%s", bg.shape, piece.shape)

    # Apply Sobel edge detection to both images
    bg_edges = _sobel(bg)
    piece_edges = _sobel(piece)

    # Template matching with multiple methods
    methods = [cv2.TM_CCOEFF_NORMED, cv2.TM_CCORR_NORMED]
    results: list[tuple[int, float]] = []

    for method in methods:
        matched = cv2.matchTemplate(bg_edges, piece_edges, method)
        _, max_val, _, max_loc = cv2.minMaxLoc(matched)
        results.append((max_loc[0], max_val))

    # Also try with Canny edges
    bg_canny = cv2.Canny(bg, 100, 200)
    piece_canny = cv2.Canny(piece, 100, 200)
    matched = cv2.matchTemplate(bg_canny, piece_canny, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(matched)
    results.append((max_loc[0], max_val))

    # Sort by confidence and return the best position
    results.sort(key=lambda x: x[1], reverse=True)
    best_pos = results[0][0]

    logger.info(
        "TikTok captcha gap position: %d (confidence: %.3f, all: %s)",
        best_pos, results[0][1], [(p, round(c, 3)) for p, c in results],
    )
    return best_pos


def _generate_human_trajectory(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    num_steps: int = 50,
) -> list[tuple[float, float, float]]:
    """Generate a human-like mouse trajectory from start to end.

    Uses a Bezier curve with random jitter to simulate human movement.
    Returns a list of (x, y, delay_ms) tuples.
    """
    points: list[tuple[float, float, float]] = []

    # Control points for a slight curve (humans don't move in straight lines)
    ctrl_x = start_x + (end_x - start_x) * 0.5 + random.uniform(-20, 20)
    ctrl_y = start_y + random.uniform(-5, 5)

    for i in range(num_steps + 1):
        t = i / num_steps
        # Quadratic Bezier curve
        x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t**2 * end_x
        y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t**2 * end_y

        # Add small random jitter
        x += random.uniform(-1, 1)
        y += random.uniform(-1, 1)

        # Non-uniform timing (humans slow down near the target)
        if i < num_steps * 0.7:
            delay = random.uniform(5, 15)  # Fast initial movement
        else:
            delay = random.uniform(10, 30)  # Slow down near target

        points.append((x, y, delay))

    # Ensure exact end position
    points[-1] = (end_x, end_y, 20)
    return points


async def solve_slider_captcha(page, gap_x: int) -> bool:
    """Solve a TikTok slider captcha by dragging the slider.

    Uses Playwright's native ``page.mouse`` API which dispatches CDP
    ``Input.dispatchMouseEvent`` calls, producing ``isTrusted=true`` events.

    Args:
        page: Playwright Page object.
        gap_x: The X pixel position of the gap in the background image
                (from ``find_gap_position``, in natural image coordinates).

    Returns:
        True if the captcha was solved, False otherwise.
    """
    # Find the captcha container and extract image info
    captcha_info = await page.evaluate("""() => {
        // Find the captcha container
        const container = document.querySelector('.captcha-verify-container');
        if (!container) return {error: 'no captcha-verify-container'};

        // Find the captcha images (alt="Captcha", data:image/webp)
        const imgs = container.querySelectorAll('img[alt="Captcha"]');
        if (imgs.length < 2) return {error: 'not enough captcha images', count: imgs.length};

        const bg = imgs[0];
        const piece = imgs[1];

        const bgRect = bg.getBoundingClientRect();
        const pieceRect = piece.getBoundingClientRect();

        // Find the slider button
        const slider = container.querySelector('#captcha_slide_button');
        const sliderRect = slider ? slider.getBoundingClientRect() : null;

        // Remove disabled class from slider
        if (slider) {
            slider.classList.remove('TUXButton--disabled');
            slider.removeAttribute('aria-disabled');
            slider.disabled = false;
        }

        return {
            bg: {
                src: bg.src,
                naturalWidth: bg.naturalWidth,
                naturalHeight: bg.naturalHeight,
                displayWidth: bgRect.width,
                displayHeight: bgRect.height,
                x: bgRect.x, y: bgRect.y,
            },
            piece: {
                src: piece.src,
                naturalWidth: piece.naturalWidth,
                naturalHeight: piece.naturalHeight,
                displayWidth: pieceRect.width,
                displayHeight: pieceRect.height,
                x: pieceRect.x, y: pieceRect.y,
            },
            slider: sliderRect ? {
                x: sliderRect.x, y: sliderRect.y, w: sliderRect.width, h: sliderRect.height,
            } : null,
        };
    }""")

    if "error" in captcha_info:
        logger.error("TikTok captcha: %s", captcha_info["error"])
        return False

    logger.info("TikTok captcha info: %s", json.dumps(captcha_info, indent=2))

    # Extract base64 data from data URLs
    bg_src = captcha_info["bg"]["src"]
    piece_src = captcha_info["piece"]["src"]

    if bg_src.startswith("data:"):
        bg_b64 = bg_src.split(",")[1]
    else:
        logger.error("TikTok captcha: bg image is not a data URL")
        return False

    if piece_src.startswith("data:"):
        piece_b64 = piece_src.split(",")[1]
    else:
        logger.error("TikTok captcha: piece image is not a data URL")
        return False

    # Find the gap position in the natural image
    gap_x_natural = find_gap_position(bg_b64, piece_b64)

    # Scale to displayed image size
    bg_natural_w = captcha_info["bg"]["naturalWidth"]
    bg_display_w = captcha_info["bg"]["displayWidth"]
    scale = bg_display_w / bg_natural_w if bg_natural_w > 0 else 1.0

    # The drag distance is the gap position minus the initial piece position
    # The piece starts at the left edge of the captcha area
    # The slider starts at the left edge of the slider track
    # We need to drag the slider by the gap distance (scaled)
    drag_distance = int(gap_x_natural * scale)

    logger.info(
        "TikTok captcha: gap_x_natural=%d, scale=%.3f, drag_distance=%d, "
        "bg_natural=%dx%d, bg_display=%.0fx%.0f",
        gap_x_natural, scale, drag_distance,
        bg_natural_w, captcha_info["bg"]["naturalHeight"],
        bg_display_w, captcha_info["bg"]["displayHeight"],
    )

    if not captcha_info["slider"]:
        logger.error("TikTok captcha: slider button not found")
        return False

    # Slider starting position (center of the slider button)
    start_x = captcha_info["slider"]["x"] + captcha_info["slider"]["w"] / 2
    start_y = captcha_info["slider"]["y"] + captcha_info["slider"]["h"] / 2

    # Target position
    end_x = start_x + drag_distance
    end_y = start_y + random.uniform(-2, 2)

    logger.info(
        "TikTok captcha: dragging slider from (%.1f, %.1f) to (%.1f, %.1f)",
        start_x, start_y, end_x, end_y,
    )

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
            if (!container) return true;  // Container gone = solved
            const style = window.getComputedStyle(container);
            return style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0';
        }"""
    )

    if captcha_solved:
        logger.info("TikTok captcha: SOLVED")
    else:
        logger.warning("TikTok captcha: NOT solved (container still visible)")

    return captcha_solved


async def solve_captcha_and_save(page, bio: str) -> bool:
    """Complete flow: fill bio, click Save, solve captcha if it appears.

    Args:
        page: Playwright Page object (already on the TikTok profile page).
        bio: The new bio text to set.

    Returns:
        True if the bio was saved successfully, False otherwise.
    """
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

    logger.info("TikTok captcha: appeared, solving...")

    # Try solving up to 3 times
    for attempt in range(3):
        logger.info("TikTok captcha: attempt %d", attempt + 1)
        solved = await solve_slider_captcha(page, 0)  # gap_x will be recalculated inside

        if solved:
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

        if attempt < 2:
            # Refresh the captcha
            refresh_btn = await page.query_selector(
                '[class*="captcha"] [class*="refresh"], [class*="captcha"] button[class*="icon"]'
            )
            if refresh_btn:
                await refresh_btn.click()
                await asyncio.sleep(2)
            else:
                # Try clicking a refresh icon
                await page.evaluate("""() => {
                    const container = document.querySelector('.captcha-verify-container');
                    if (container) {
                        const btns = container.querySelectorAll('button, [class*="refresh"], [class*="icon"]');
                        btns.forEach(b => { if (b.textContent.includes('refresh') || b.className.includes('refresh')) b.click(); });
                    }
                }""")
                await asyncio.sleep(2)

    logger.error("TikTok captcha: failed to solve after 3 attempts")
    return False
