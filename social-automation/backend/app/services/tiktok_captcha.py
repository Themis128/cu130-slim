"""TikTok slider captcha solver — OpenCV gap detection + human-like mouse drag.

Uses OpenCV template matching to find the puzzle gap position, then
Playwright's native ``page.mouse`` API (CDP-based, ``isTrusted=true``) to
drag the slider with a human-like trajectory.

Based on open-source implementations:
- Gisnsl/tiktok-captcha-solver (MIT) — OpenCV Sobel edge detection
- vsmutok/PuzzleCaptchaSolver (MIT) — template matching
- DEV Community CDP mouse events article — isTrusted bypass

Key insight: Playwright's ``page.mouse.move()`` uses CDP
``Input.dispatchMouseEvent`` which produces events with ``isTrusted=true``.
Synthetic JavaScript ``MouseEvent`` dispatch produces ``isTrusted=false``
which TikTok's captcha rejects.
"""

from __future__ import annotations

import asyncio
import base64
import io
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
        The X pixel position of the gap in the background image.
    """
    bg = _decode_b64_image(bg_b64)
    piece = _decode_b64_image(piece_b64)

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

    logger.info("TikTok captcha gap position: %d (confidence: %.3f)", best_pos, results[0][1])
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

    The trajectory avoids the "staircase" pattern that anti-bot systems
    detect (uniform steps with fixed time deltas).
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
                (from ``find_gap_position``).

    Returns:
        True if the captcha was solved, False otherwise.
    """
    # Find the slider element
    slider = await page.query_selector(".secsdk-captcha-drag-icon")
    if not slider:
        logger.error("TikTok captcha: slider element not found")
        return False

    slider_box = await slider.bounding_box()
    if not slider_box:
        logger.error("TikTok captcha: slider has no bounding box")
        return False

    # Find the background image to get the scale factor
    bg_img = await page.query_selector(
        'img[alt="Captcha"]'
    )
    if not bg_img:
        logger.error("TikTok captcha: background image not found")
        return False

    bg_box = await bg_img.bounding_box()
    if not bg_box:
        logger.error("TikTok captcha: background image has no bounding box")
        return False

    # The gap_x is in the original image coordinate space.
    # We need to scale it to the displayed image size.
    # The background image is typically 347px wide in the raw image.
    # The displayed width is bg_box["width"].
    raw_width = 347  # Standard TikTok captcha background width
    scale = bg_box["width"] / raw_width
    drag_distance = int(gap_x * scale)

    # Slider starting position (center of the slider button)
    start_x = slider_box["x"] + slider_box["width"] / 2
    start_y = slider_box["y"] + slider_box["height"] / 2

    # Target position
    end_x = start_x + drag_distance
    end_y = start_y + random.uniform(-2, 2)

    logger.info(
        "TikTok captcha: dragging slider from (%.1f, %.1f) to (%.1f, %.1f), "
        "gap_x=%d, scale=%.3f, drag_distance=%d",
        start_x, start_y, end_x, end_y, gap_x, scale, drag_distance,
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
    await asyncio.sleep(2)

    # Check if the captcha was solved
    captcha_solved = await page.evaluate(
        """() => {
            const dialogs = document.querySelectorAll('[role="dialog"]');
            for (const d of dialogs) {
                if (d.textContent.includes('puzzle') || d.textContent.includes('Drag')) {
                    return false;  // Captcha dialog still present
                }
            }
            return true;  // No captcha dialog = solved
        }"""
    )

    if captcha_solved:
        logger.info("TikTok captcha: SOLVED")
    else:
        logger.warning("TikTok captcha: NOT solved (dialog still present)")

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
    await asyncio.sleep(3)

    # Check if a captcha appeared
    captcha_present = await page.evaluate(
        """() => {
            const dialogs = document.querySelectorAll('[role="dialog"]');
            for (const d of dialogs) {
                if (d.textContent.includes('puzzle') || d.textContent.includes('Drag')) {
                    return true;
                }
            }
            return false;
        }"""
    )

    if captcha_present:
        logger.info("TikTok captcha: appeared, solving...")

        # Extract captcha images
        captcha_data = await page.evaluate(
            """() => {
                const dialogs = document.querySelectorAll('[role="dialog"]');
                for (const d of dialogs) {
                    if (d.textContent.includes('puzzle') || d.textContent.includes('Drag')) {
                        const imgs = d.querySelectorAll('img');
                        if (imgs.length >= 2) {
                            return {
                                bg: imgs[0].src,
                                piece: imgs[1].src
                            };
                        }
                    }
                }
                return null;
            }"""
        )

        if not captcha_data:
            logger.error("TikTok captcha: could not extract images")
            return False

        # Extract base64 data from data URLs
        bg_b64 = captcha_data["bg"].split(",")[1] if "," in captcha_data["bg"] else captcha_data["bg"]
        piece_b64 = captcha_data["piece"].split(",")[1] if "," in captcha_data["piece"] else captcha_data["piece"]

        # Find the gap position
        gap_x = find_gap_position(bg_b64, piece_b64)

        # Solve the captcha
        solved = await solve_slider_captcha(page, gap_x)
        if not solved:
            logger.warning("TikTok captcha: first attempt failed, retrying...")

            # Try refreshing the captcha and solving again
            refresh_btn = await page.query_selector(
                'button[class*="secsdk-captcha-refresh"]'
            )
            if refresh_btn:
                await refresh_btn.click()
                await asyncio.sleep(2)

                # Re-extract images
                captcha_data = await page.evaluate(
                    """() => {
                        const dialogs = document.querySelectorAll('[role="dialog"]');
                        for (const d of dialogs) {
                            if (d.textContent.includes('puzzle') || d.textContent.includes('Drag')) {
                                const imgs = d.querySelectorAll('img');
                                if (imgs.length >= 2) {
                                    return {bg: imgs[0].src, piece: imgs[1].src};
                                }
                            }
                        }
                        return null;
                    }"""
                )

                if captcha_data:
                    bg_b64 = captcha_data["bg"].split(",")[1] if "," in captcha_data["bg"] else captcha_data["bg"]
                    piece_b64 = captcha_data["piece"].split(",")[1] if "," in captcha_data["piece"] else captcha_data["piece"]
                    gap_x = find_gap_position(bg_b64, piece_b64)
                    solved = await solve_slider_captcha(page, gap_x)

        if not solved:
            logger.error("TikTok captcha: failed to solve after retries")
            return False

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
