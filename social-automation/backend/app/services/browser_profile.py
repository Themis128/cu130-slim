"""Browser-automated profile updates for Facebook and LinkedIn.

Facebook and LinkedIn do not expose profile-write APIs for personal
profiles.  This service uses Playwright to drive the web UI exactly as a
human would.  A persistent browser context is used per account and its
storage state (cookies + localStorage) is persisted in the database so
2FA / CAPTCHA only needs to be solved once.

Workflow:
    1. POST /profile/{account_id}/login with username + password
    2. If 2FA is required, the service returns a challenge and the user
       completes it (or the account becomes usable after the fact).
    3. Storage state is saved to ``social_accounts.meta_data``.
    4. Subsequent profile reads/writes load the storage state into a
       headless browser and run the UI automation.

Security:
    - Passwords are never persisted; only the resulting storage state.
    - Sessions are encrypted like other account metadata.
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class BrowserProfileError(Exception):
    """Raised when a browser automation step fails."""

    def __init__(self, status_code: int, detail: str, *, retryable: bool = False) -> None:
        self.status_code = status_code
        self.detail = detail
        self.retryable = retryable
        super().__init__(f"Browser profile error {status_code}: {detail}")


@dataclass
class BrowserSession:
    """In-memory handle for a loaded browser context."""
    browser: Any
    context: Any
    page: Any
    storage_state: dict | None = None


class BrowserProfileService:
    """Playwright-based profile automation for Facebook and LinkedIn."""

    def __init__(self, storage_state: dict | None = None) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserProfileError(
                500,
                "Playwright is not installed. Run: pip install playwright",
            ) from e

        self._playwright = async_playwright
        self._storage_state = storage_state

    def _load_storage_state(self, raw: dict | str | None) -> dict | None:
        """Load a Playwright storage state from the account meta_data."""
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return None
        return raw if isinstance(raw, dict) and raw.get("cookies") else None

    async def _launch_context(self, storage_state: dict | None = None) -> BrowserSession:
        """Launch a headless Chromium browser with an optional storage state."""
        p = await self._playwright().start()
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 720},
            # Force English UI: profile-edit selectors below match English
            # text ("About", "Save"), which localised pages translate.
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            ),
        )
        # Set a webdriver=false property to reduce headless detection.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()
        return BrowserSession(browser=browser, context=context, page=page)

    async def _close_session(self, session: BrowserSession) -> None:
        """Close the browser session."""
        await session.context.close()
        await session.browser.close()

    async def _extract_storage_state(self, session: BrowserSession) -> dict:
        """Return the current storage state for persistence."""
        return await session.context.storage_state()

    # ── Facebook ────────────────────────────────────────────────────────────

    async def login_facebook(self, username: str, password: str, verification_code: str | None = None) -> dict[str, Any]:
        """Log in to Facebook via the web UI and return the storage state."""
        session = await self._launch_context()
        try:
            page = session.page
            await page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
            # Facebook renders the login form with JS; wait for the email input.
            await page.wait_for_selector('input[name="email"]', timeout=30000)
            await page.fill('input[name="email"]', username)
            await page.fill('input[name="pass"]', password)
            # Submit by pressing Enter; the submit button is a locale-specific div on Facebook.
            await page.press('input[name="pass"]', "Enter")

            # Wait for either the home page or a challenge/2FA page
            await page.wait_for_load_state("networkidle")

            # Check for 2FA / challenge
            approvals_code_count = await page.locator("input#approvals_code").count()
            if "checkpoint" in page.url or approvals_code_count > 0:
                if verification_code:
                    await page.fill("input#approvals_code", verification_code)
                    await page.press("input#approvals_code", "Enter")
                    await page.wait_for_load_state("networkidle")
                    if "facebook.com" in page.url and ("home" in page.url or "/" in page.url):
                        return {
                            "success": True,
                            "two_factor_required": False,
                            "message": "Facebook login successful",
                            "storage_state": await self._extract_storage_state(session),
                        }
                return {
                    "success": False,
                    "two_factor_required": True,
                    "message": "Facebook 2FA required. Provide verification_code and retry.",
                    "url": page.url,
                    "storage_state": await self._extract_storage_state(session),
                }

            # Check if we made it home
            if "facebook.com" in page.url and ("home" in page.url or "/" in page.url):
                storage_state = await self._extract_storage_state(session)
                return {
                    "success": True,
                    "two_factor_required": False,
                    "message": "Facebook login successful",
                    "storage_state": storage_state,
                }

            raise BrowserProfileError(401, f"Facebook login failed. Current URL: {page.url}")
        finally:
            await self._close_session(session)

    async def get_facebook_profile(self, storage_state: dict) -> dict[str, Any]:
        """Fetch the current Facebook personal profile page."""
        session = await self._launch_context(storage_state)
        try:
            page = session.page
            # Try the profile page
            await page.goto("https://www.facebook.com/me")
            await page.wait_for_load_state("networkidle")

            result: dict[str, Any] = {
                "url": page.url,
                "name": None,
                "about": None,
                "profile_pic_url": None,
            }

            # Attempt to read the name
            title = await page.title()
            if title and title not in ("Facebook", "Log in"):
                result["name"] = title.split("(")[0].strip()

            # Attempt to read the "About" intro text
            intro = await page.locator("[data-pagelet='ProfileActions']").first.inner_text(timeout=2000).catch(lambda _: "")
            if intro:
                result["about"] = intro.strip()

            return result
        finally:
            await self._close_session(session)

    async def update_facebook_about(self, storage_state: dict, text: str) -> dict[str, Any]:
        """Update the Facebook personal profile 'About' text.

        This opens the Edit Profile modal, focuses the bio field, and saves.
        Facebook's selectors change frequently; the method is best-effort.
        """
        session = await self._launch_context(storage_state)
        try:
            page = session.page
            await page.goto("https://www.facebook.com/profile.php?sk=about")
            await page.wait_for_load_state("networkidle")

            # Look for an edit bio / add bio button
            add_bio_btn = page.locator("text='Add Bio'").first
            edit_bio_btn = page.locator("text='Edit Bio'").first

            if await edit_bio_btn.count() > 0:
                await edit_bio_btn.click()
            elif await add_bio_btn.count() > 0:
                await add_bio_btn.click()
            else:
                raise BrowserProfileError(
                    501,
                    "Could not locate the Facebook 'Add/Edit Bio' control. "
                    "The selector may need updating for the current Facebook layout.",
                    retryable=True,
                )

            # Fill the bio textarea
            bio_input = page.locator("textarea[aria-label='Bio']").first
            if await bio_input.count() == 0:
                bio_input = page.locator("textarea").first
            await bio_input.fill(text)

            # Save
            await page.click("button:has-text('Save')")
            await page.wait_for_load_state("networkidle")

            return {"success": True, "updated_fields": ["about"], "message": "Facebook bio updated"}
        finally:
            await self._close_session(session)

    async def update_facebook_profile_picture(
        self, storage_state: dict, image_bytes: bytes, filename: str = "profile.jpg"
    ) -> dict[str, Any]:
        """Upload a new Facebook personal profile picture."""
        session = await self._launch_context(storage_state)
        try:
            page = session.page
            await page.goto("https://www.facebook.com/profile.php")
            await page.wait_for_load_state("networkidle")

            # Click update profile picture button
            update_btn = page.locator("[aria-label='Update profile picture']").first
            if await update_btn.count() == 0:
                update_btn = page.locator("text='Update profile picture'").first
            if await update_btn.count() == 0:
                raise BrowserProfileError(501, "Could not locate the Facebook 'Update profile picture' control")

            await update_btn.click()

            with tempfile.NamedTemporaryFile(suffix=f".{filename.split('.')[-1]}", delete=False) as f:
                f.write(image_bytes)
                temp_path = f.name

            try:
                # Facebook often uses a hidden input after the button click
                input_selector = "input[type='file'][accept*='image']"
                await page.locator(input_selector).set_input_files(temp_path)
                await page.wait_for_load_state("networkidle")

                # Confirm
                save_btn = page.locator("button:has-text('Save')").first
                if await save_btn.count() > 0:
                    await save_btn.click()
                    await page.wait_for_load_state("networkidle")
            finally:
                import os
                os.unlink(temp_path)

            return {"success": True, "updated_fields": ["profile_picture"], "message": "Facebook profile picture updated"}
        finally:
            await self._close_session(session)

    async def _settle(self, page: Any, timeout: int = 20000) -> None:
        """Best-effort wait for the page to calm down.

        ``networkidle`` never fires on pages with long-polling (LinkedIn feed,
        checkpoint challenges), so treat it as best-effort and settle briefly.
        """
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
        await page.wait_for_timeout(1500)

    # ── LinkedIn ────────────────────────────────────────────────────────────

    async def login_linkedin(self, username: str, password: str, verification_code: str | None = None) -> dict[str, Any]:
        """Log in to LinkedIn via the web UI and return the storage state."""
        session = await self._launch_context()
        try:
            page = session.page
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            # LinkedIn renders the login form with JS. The legacy form uses
            # input[name="session_key"]; the newer auth page uses a plain
            # input[type="email"] with randomized ids/classes (and no
            # type=submit button), so match the first visible pair and submit
            # via Enter, falling back to the "Sign in" button.
            await page.wait_for_selector(
                'input[name="session_key"]:visible, input[type="email"]:visible', timeout=30000
            )
            user_input = page.locator(
                'input[name="session_key"]:visible, input[type="email"]:visible'
            ).first
            await user_input.fill(username)
            pw_input = page.locator(
                'input[name="session_password"]:visible, input[type="password"]:visible'
            ).first
            await pw_input.fill(password)
            await pw_input.press("Enter")
            await page.wait_for_timeout(3000)
            if page.url.rstrip("/").endswith("/login"):
                sign_in = page.get_by_role("button", name="Sign in", exact=True)
                if await sign_in.count() > 0:
                    await sign_in.first.click()
            await self._settle(page)

            # Check for 2FA / challenge
            if page.url.startswith("https://www.linkedin.com/checkpoint/"):
                if verification_code:
                    # LinkedIn 2FA input name is typically "input[otp" or "#input__phone_verification_pin"
                    for sel in ("input[name='pin']", "input#input__phone_verification_pin", "input[autocomplete='one-time-code']"):
                        try:
                            await page.fill(sel, verification_code)
                            await page.press(sel, "Enter")
                            await self._settle(page)
                            break
                        except Exception:
                            pass
                    if "linkedin.com/feed" in page.url or "linkedin.com/in/" in page.url:
                        return {
                            "success": True,
                            "two_factor_required": False,
                            "message": "LinkedIn login successful",
                            "storage_state": await self._extract_storage_state(session),
                        }
                return {
                    "success": False,
                    "two_factor_required": True,
                    "message": "LinkedIn 2FA / verification required. Provide verification_code and retry.",
                    "url": page.url,
                    "storage_state": await self._extract_storage_state(session),
                }

            if "linkedin.com/feed" in page.url or "linkedin.com/in/" in page.url:
                storage_state = await self._extract_storage_state(session)
                return {
                    "success": True,
                    "two_factor_required": False,
                    "message": "LinkedIn login successful",
                    "storage_state": storage_state,
                }

            raise BrowserProfileError(401, f"LinkedIn login failed. Current URL: {page.url}")
        finally:
            await self._close_session(session)

    async def get_linkedin_profile(self, storage_state: dict) -> dict[str, Any]:
        """Fetch the current LinkedIn personal profile page."""
        session = await self._launch_context(storage_state)
        try:
            page = session.page
            await page.goto("https://www.linkedin.com/in/me/")
            await self._settle(page)

            result: dict[str, Any] = {"url": page.url, "name": None, "headline": None, "about": None}

            # Legacy layout: name in h1.text-heading-xlarge, headline in
            # .text-body-medium. New layout (2026): the name is an h2 inside
            # the top-card section and the headline is the next text line.
            name_h1 = page.locator("h1.text-heading-xlarge").first
            if await name_h1.count() > 0:
                result["name"] = (await name_h1.inner_text()).strip()

            if not result["name"]:
                secs = page.locator("section:has(h2)")
                for i in range(await secs.count()):
                    sec = secs.nth(i)
                    h2 = (await sec.locator("h2").first.inner_text()).strip()
                    if h2 and not h2.endswith("notifications"):
                        result["name"] = h2
                        lines = [
                            ln.strip()
                            for ln in (await sec.inner_text()).splitlines()
                            if ln.strip()
                        ]
                        for j, ln in enumerate(lines):
                            if ln == h2 and j + 1 < len(lines):
                                result["headline"] = lines[j + 1]
                                break
                        break

            # About: legacy used .pv-shared-text-with-see-more; new layout is
            # a section with an h2 "About" whose body text follows the header.
            about_section = page.locator("section:has(h2:has-text('About')) .pv-shared-text-with-see-more").first
            if await about_section.count() > 0:
                result["about"] = await about_section.inner_text()
            else:
                about_sec = page.locator("section:has(h2:has-text('About'))").first
                if await about_sec.count() > 0:
                    text = await about_sec.inner_text()
                    lines = [ln for ln in text.splitlines()]
                    # Keep only lines between the "About" header and the next
                    # section header — the section element wraps neighbouring
                    # content on the new layout.
                    start = lines.index("About") + 1 if "About" in lines else 0
                    end = len(lines)
                    for marker in ("Featured", "Activity", "Experience"):
                        if marker in lines:
                            end = min(end, lines.index(marker))
                    about = "\n".join(lines[start:end]).strip()
                    if about:
                        result["about"] = about

            return result
        finally:
            await self._close_session(session)

    async def update_linkedin_headline(self, storage_state: dict, headline: str) -> dict[str, Any]:
        """Update the LinkedIn profile headline."""
        session = await self._launch_context(storage_state)
        try:
            page = session.page
            await page.goto("https://www.linkedin.com/in/me/edit/intro/")
            await self._settle(page)

            # The intro page no longer renders a headline input; it shows an
            # "Update headline" prompt whose clickable ancestor opens the
            # editor (regular Playwright clicks time out on it).
            span = page.get_by_text("Update headline", exact=True).first
            if await span.count() == 0:
                raise BrowserProfileError(501, "Could not locate the LinkedIn 'Update headline' prompt")
            handle = await span.evaluate_handle(
                "el => el.closest('button, a, [role=\\'button\\']') || el.parentElement"
            )
            await page.evaluate("el => el.click()", handle)
            await page.wait_for_timeout(4000)

            editor = None
            for sel in (
                "div[role='dialog'] textarea:visible",
                "textarea:visible",
                "div[role='dialog'] input:visible",
                '[contenteditable="true"]:visible',
            ):
                loc = page.locator(sel)
                if await loc.count() > 0:
                    editor = loc.first
                    break
            if editor is None:
                raise BrowserProfileError(501, "Could not locate the LinkedIn headline editor")

            await editor.fill(headline)

            save_btn = page.get_by_role("button", name="Save", exact=True).first
            if await save_btn.count() == 0:
                save_btn = page.locator("button:has-text('Save')").first
            await save_btn.click()
            await self._settle(page)

            return {"success": True, "updated_fields": ["headline"], "message": "LinkedIn headline updated"}
        finally:
            await self._close_session(session)

    async def update_linkedin_about(self, storage_state: dict, text: str) -> dict[str, Any]:
        """Update the LinkedIn 'About' section."""
        session = await self._launch_context(storage_state)
        try:
            page = session.page
            # /edit/about and /editforms/about are dead URLs now; the working
            # path is the "Edit about" pencil on the profile, which opens the
            # summary edit form with a rich-text contenteditable editor.
            await page.goto("https://www.linkedin.com/in/me/")
            await self._settle(page)

            about_btn = page.locator('button[aria-label="Edit about"], a[aria-label="Edit about"]').first
            if await about_btn.count() == 0:
                raise BrowserProfileError(501, "Could not locate the LinkedIn 'Edit about' button")
            await about_btn.click()
            await page.wait_for_timeout(5000)

            editor = page.locator('[contenteditable="true"]').first
            if await editor.count() == 0:
                raise BrowserProfileError(501, "Could not locate the LinkedIn About editor")

            await editor.click()
            await editor.fill(text)

            save_btn = page.get_by_role("button", name="Save", exact=True).first
            if await save_btn.count() == 0:
                save_btn = page.locator("button:has-text('Save')").first
            await save_btn.click()
            await self._settle(page)

            return {"success": True, "updated_fields": ["about"], "message": "LinkedIn About updated"}
        finally:
            await self._close_session(session)
