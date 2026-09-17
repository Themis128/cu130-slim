"""HTTP client for the browser-novnc bridge container.

The browser-novnc container runs a headed Chromium with noVNC for visual
login and a FastAPI bridge on port 9223.  This client wraps the bridge
endpoints so the SocialAuto backend can use the logged-in browser session
as a fallback when the aiograpi-rest sidecar fails (challenge_required,
fingerprint mismatch, etc.).

Currently supports Instagram profile read/write via the browser's
authenticated fetch to Instagram's internal web API.

Browser bridge access is coordinated by app.services.browser_orchestrator
to prevent concurrent navigation by multiple workers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class BrowserBridgeError(Exception):
    """Raised when the browser bridge returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Browser bridge error {status_code}: {detail}")


class BrowserBridgeClient:
    """Thin HTTP wrapper around the browser-novnc bridge API."""

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        platform: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Sent as the X-Platform header on every request — the bridge uses
        # it to attribute busy-holds and reject foreign-platform calls while
        # a session is mid-flow.
        self._platform = platform

    def _headers(self) -> dict[str, str]:
        return {"X-Platform": self._platform} if self._platform else {}

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.get(f"{self._base_url}/health")
            resp.raise_for_status()
            return resp.json()

    async def start_session(self, platform: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/start",
                json={"platform": platform},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def session_status(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.get(f"{self._base_url}/session/status")
            resp.raise_for_status()
            return resp.json()

    async def ensure_session(self, platform: str = "facebook") -> dict[str, Any]:
        """Check if a browser session is active and logged in.

        If the session is down or not logged in, starts a new session
        and returns a status dict with:
            - status: "active" | "waiting" | "error"
            - message: human-readable detail
            - novnc_url: noVNC viewer URL (when waiting)
        """
        try:
            status = await self.session_status()
        except Exception:
            status = {"status": "error", "message": "Bridge unreachable"}

        # A session for this platform is mid-login — give the bridge's
        # detection loop time to observe the authenticated URL/cookies
        # instead of erroring instantly.
        if (
            status.get("status") in ("waiting", "extracting")
            and status.get("platform") == platform
        ):
            for _ in range(90):
                await asyncio.sleep(1)
                try:
                    status = await self.session_status()
                except Exception:
                    status = {"status": "error"}
                if status.get("status") not in ("waiting", "extracting"):
                    break

        # Session is active and logged in — bridge returns "active" or "done"
        # (after cookie extraction) with cookies_found populated. The session
        # must belong to the requested platform — a logged-in Facebook session
        # must not satisfy a Twitter check (the cookie jar is platform-scoped
        # at extraction time, and the browser may be parked on a login page).
        if (
            status.get("status") in ("active", "done")
            and status.get("platform") == platform
            and status.get("cookies_found")
        ):
            return {"status": "active", "message": "Session active"}

        # Try extracting cookies — the browser may be logged in but the
        # session status hasn't been updated yet
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
                resp = await client.post(f"{self._base_url}/session/extract")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("cookies_found") and data.get("platform") == platform:
                        return {"status": "active", "message": "Session active"}
        except Exception:
            pass

        # Session is waiting, error, or has no cookies — restart it
        try:
            await self.start_session(platform)
        except BrowserBridgeError:
            pass

        return {
            "status": "waiting",
            "message": f"Browser session not logged in. Open noVNC and log in to {platform}.",
            "novnc_url": "/novnc/vnc.html?autoconnect=1&resize=scale",
        }

    async def session_login(self, username: str, password: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/login",
                json={"username": username, "password": password},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def mouse_click(self, x: float, y: float) -> dict[str, Any]:
        """Trusted mouse click at viewport coordinates via the bridge.

        Unlike JS ``el.click()``, this dispatches real input events through
        Playwright — required where sites (e.g. X/Arkose) reject synthetic
        clicks.
        """
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/mouse-click",
                json={"x": x, "y": y},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def is_twitter_logged_in(self) -> dict[str, Any]:
        """Probe the live page for an authenticated x.com session.

        If the shared page was navigated off x.com by another platform's
        flow, jump to x.com/home first — the probe is only meaningful on
        twitter's own SPA.
        """
        probe_expr = (
            "() => ({url: location.href, loggedIn: !!document.querySelector("
            "'[data-testid=SideNav_AccountSwitcher_Button]')})"
        )
        try:
            probe = await self.evaluate(probe_expr)
        except BrowserBridgeError as exc:
            return {"logged_in": False, "url": None, "error": exc.detail}
        res = probe.get("result", probe) if isinstance(probe, dict) else probe
        if not isinstance(res, dict):
            return {"logged_in": False, "url": None}
        url = res.get("url") or ""
        if not res.get("loggedIn") and "x.com" not in url and "twitter.com" not in url:
            try:
                await self.navigate("https://x.com/home")
                await asyncio.sleep(8)
                probe = await self.evaluate(probe_expr)
            except BrowserBridgeError as exc:
                return {"logged_in": False, "url": url, "error": exc.detail}
            res = probe.get("result", probe) if isinstance(probe, dict) else probe
            if not isinstance(res, dict):
                return {"logged_in": False, "url": url}
        return {"logged_in": bool(res.get("loggedIn")), "url": res.get("url")}

    async def _click_visible_continue(self) -> bool:
        """Find a visible Continue/Next/Log-in button and mouse-click it.

        The funnel renders localized labels (the account's locale can make
        X show Greek 'Συνέχεια' / 'Σύνδεση'), so match several variants.
        """
        probe = await self.evaluate(
            """() => {
                const labels = ['Continue', 'Next', 'Log in', 'Sign in',
                                'Συνέχεια', 'Σύνδεση', 'Επόμενο'];
                const els = [...document.querySelectorAll('button,[role=button],input[type=submit]')];
                const c = els.find(e => {
                    const t = (e.innerText || e.value || '').trim();
                    return labels.includes(t) && e.offsetParent !== null;
                });
                if (!c) return {found: false};
                const b = c.getBoundingClientRect();
                return {found: true, x: b.x + b.width / 2, y: b.y + b.height / 2, label: (c.innerText||'').trim()};
            }"""
        )
        res = probe.get("result", probe) if isinstance(probe, dict) else probe
        if not isinstance(res, dict) or not res.get("found"):
            return False
        await self.mouse_click(res["x"], res["y"])
        return True

    async def twitter_login(
        self, username: str, password: str, timeout_s: float = 90.0
    ) -> dict[str, Any]:
        """Drive X's two-step onboarding login flow with stored credentials.

        Flow quirks discovered empirically (Sept 2026):
        - ``x.com/login`` now redirects to ``/i/jf/onboarding/web?mode=login``
          — that funnel IS the login page.
        - The identifier field accepts the account's login username or
          email (``TWITTER_LOGIN_USERNAME``/``TWITTER_LOGIN_EMAIL``).
        - Two steps: identifier -> Continue -> password -> Continue.
          Filling the password input on step 1 flips the funnel into
          signup ("Email signups are only allowed on the apps").
        - Click the **visible** Continue via real mouse events — the page
          renders duplicate hidden buttons and JS ``.click()`` is untrusted.
        - On step 2 the username input is disabled/prefilled; fill only the
          enabled ``input[name=password]``.
        """
        await self.navigate("https://x.com/i/flow/login")

        # Step 1 — wait for the identifier field, then enter the handle.
        # An already-authenticated profile redirects off the login page, so
        # check for the account switcher each iteration.
        deadline = asyncio.get_event_loop().time() + timeout_s
        filled = False
        while asyncio.get_event_loop().time() < deadline:
            probe = await self.evaluate(
                "() => ({form: !!document.querySelector('input[name=username_or_email]'),"
                " loggedIn: !!document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]')})"
            )
            res = probe.get("result", probe) if isinstance(probe, dict) else probe
            if isinstance(res, dict):
                if res.get("loggedIn"):
                    return {"status": "logged_in", "url": "already authenticated"}
                if res.get("form"):
                    await self.fill("input[name=username_or_email]", username)
                    filled = True
                    break
            await asyncio.sleep(1)
        if not filled:
            return {"status": "error", "error": "login form did not render"}

        await asyncio.sleep(1.5)
        if not await self._click_visible_continue():
            return {"status": "error", "error": "Continue button not found (step 1)"}

        # Step 2 — wait for the password step, then fill the enabled field.
        filled = False
        while asyncio.get_event_loop().time() < deadline:
            probe = await self.evaluate(
                """() => ({
                    pwReady: !![...document.querySelectorAll('input[name=password]')]
                        .find(i => !i.disabled && i.offsetParent !== null),
                    loggedIn: !!document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]'),
                    arkose: !!document.querySelector('iframe[src*=arkose],[id*=arkose]'),
                    err: document.body.innerText.includes('password you entered is incorrect')
                        || document.body.innerText.includes('Wrong password'),
                })"""
            )
            res = probe.get("result", probe) if isinstance(probe, dict) else probe
            if isinstance(res, dict):
                if res.get("loggedIn"):
                    return {"status": "logged_in", "url": "authenticated during flow"}
                if res.get("arkose"):
                    return {"status": "error", "error": "arkose captcha — manual login via noVNC required"}
                if res.get("err"):
                    return {"status": "error", "error": "x.com rejected the password (check TWITTER_LOGIN_PASSWORD)"}
                if res.get("pwReady"):
                    await self.fill("input[name=password]", password)
                    filled = True
                    break
            await asyncio.sleep(1)
        if not filled:
            return {"status": "error", "error": "password step did not render"}

        await asyncio.sleep(1.5)
        if not await self._click_visible_continue():
            return {"status": "error", "error": "Continue button not found (step 2)"}

        # Wait for authenticated state (or a rejection).
        while asyncio.get_event_loop().time() < deadline:
            state = await self.is_twitter_logged_in()
            if state.get("logged_in"):
                return {"status": "logged_in", "url": state.get("url")}
            probe = await self.evaluate(
                """() => ({
                    arkose: !!document.querySelector('iframe[src*=arkose],[id*=arkose]'),
                    err: document.body.innerText.includes('password you entered is incorrect')
                        || document.body.innerText.includes('Wrong password'),
                })"""
            )
            res = probe.get("result", probe) if isinstance(probe, dict) else probe
            if isinstance(res, dict):
                if res.get("arkose"):
                    return {"status": "error", "error": "arkose captcha — manual login via noVNC required"}
                if res.get("err"):
                    return {"status": "error", "error": "x.com rejected the password (check TWITTER_LOGIN_PASSWORD)"}
            await asyncio.sleep(2)

        return {"status": "error", "error": "login did not complete before timeout"}

    async def navigate(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/navigate",
                json={"url": url},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def extract_cookies(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(f"{self._base_url}/session/extract")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def stop_session(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(f"{self._base_url}/session/stop")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    # ── Instagram profile via browser ────────────────────────────────────

    async def get_instagram_profile(self) -> dict[str, Any]:
        """Read the Instagram profile from the logged-in browser session."""
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.get(f"{self._base_url}/profile/instagram")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def update_instagram_profile(
        self,
        full_name: str | None = None,
        biography: str | None = None,
        external_url: str | None = None,
    ) -> dict[str, Any]:
        """Update the Instagram profile via the logged-in browser session."""
        payload: dict[str, Any] = {}
        if full_name is not None:
            payload["full_name"] = full_name
        if biography is not None:
            payload["biography"] = biography
        if external_url is not None:
            payload["external_url"] = external_url
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.patch(
                f"{self._base_url}/profile/instagram",
                json=payload,
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    # ── Generic browser automation primitives ─────────────────────────────

    async def click(self, selector: str, text: str | None = None) -> dict[str, Any]:
        """Click an element via the bridge."""
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/click",
                json={"selector": selector, "text": text},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def fill(self, selector: str, value: str) -> dict[str, Any]:
        """Fill an input/textarea via the bridge."""
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/fill",
                json={"selector": selector, "value": value},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def evaluate(self, expression: str) -> dict[str, Any]:
        """Evaluate JS in the browser and return the result."""
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/evaluate",
                json={"expression": expression},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def upload_file(
        self, selector: str, file_path: str, click_selector: str | None = None
    ) -> dict[str, Any]:
        """Upload a file via the bridge's native Playwright set_input_files."""
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers()) as client:
            resp = await client.post(
                f"{self._base_url}/session/upload",
                json={
                    "selector": selector,
                    "file_path": file_path,
                    "click_selector": click_selector,
                },
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    # ── Threads profile via browser ─────────────────────────────────────

    async def get_threads_profile(self, username: str) -> dict[str, Any]:
        """Read a Threads profile by navigating to the public profile page."""
        # Prefer .com; the canonical URLs are currently www.threads.com
        await self.navigate(f"https://www.threads.com/@{username}")
        await asyncio.sleep(2)
        result = await self.evaluate("""() => {
            const body = document.body.innerText;
            const lines = body.split('\\n');
            const nameEl = document.querySelector('h1, h2');
            const displayName = nameEl ? nameEl.textContent.trim() : '';
            const username = window.location.pathname.replace(/^\\/@/, '');
            // Find the follower line, then walk backwards to find the last
            // occurrence of the handle. Bio is everything between them.
            const followerIdx = lines.findIndex(l => /\\d+ followers?/.test(l));
            let bio = '';
            if (followerIdx > 0) {
                let handleIdx = -1;
                for (let i = followerIdx - 1; i >= 0; i--) {
                    if (lines[i].trim() === username) {
                        handleIdx = i;
                        break;
                    }
                }
                if (handleIdx >= 0 && handleIdx < followerIdx) {
                    const bioLines = [];
                    for (let i = handleIdx + 1; i < followerIdx; i++) {
                        const l = lines[i].trim();
                        if (!l) continue;
                        if (l === displayName) continue;
                        bioLines.push(l);
                    }
                    bio = bioLines.join('\\n');
                }
            }
            return {
                url: document.location.href,
                username: username,
                full_name: displayName,
                biography: bio,
                raw: { sample: body.substring(0, 500) },
            };
        }""")
        return result.get("result", {})

    async def update_threads_profile(
        self,
        username: str,
        biography: str | None = None,
        full_name: str | None = None,
        website: str | None = None,
    ) -> dict[str, Any]:
        """Update Threads bio/name via the logged-in browser session.

        Threads has no official write API for profile fields, so we automate
        the web UI through the VNC browser.
        """
        updated: list[str] = []
        ignored: list[str] = []

        if full_name is not None:
            ignored.append("full_name")  # Not supported yet

        if website is not None:
            ignored.append("website")  # Not supported yet

        if biography is not None:
            try:
                await self.navigate(f"https://www.threads.com/@{username}")
                await asyncio.sleep(2)
                await self.click('div[role="button"]', "Edit profile")
                await asyncio.sleep(2)
                await self.click('div[role="button"]', "Bio")
                await asyncio.sleep(2)
                await self.fill("textarea", biography)
                await asyncio.sleep(1)
                # Click the *last* Done in the DOM (the one inside the modal)
                await self.evaluate("""(function() {
                    const all = Array.from(document.querySelectorAll('div[role="button"]'));
                    const done = all.filter(b => b.innerText.trim() === 'Done').pop();
                    if (done) { done.click(); return 'clicked'; }
                    return 'no Done';
                })()""")
                await asyncio.sleep(3)
                updated.append("biography")
            except Exception as e:
                raise BrowserBridgeError(500, f"Threads bio update failed: {e}")

        return {
            "platform": "threads",
            "status": "updated",
            "updated_fields": updated,
            "ignored_fields": ignored,
        }

    async def get_threads_settings(self, username: str) -> dict[str, Any]:
        """Read Threads profile settings from the Edit profile page.

        Returns the current state of toggles like ``show_instagram_badge`` and
        ``show_recent_views``.  Threads has no official API for these settings,
        so we read them from the web UI via the logged-in browser session.
        """
        await self.navigate(f"https://www.threads.com/@{username}")
        await asyncio.sleep(2)
        # Open the Edit profile modal
        await self.click('div[role="button"]', "Edit profile")
        await asyncio.sleep(2)

        result = await self.evaluate("""() => {
            const body = document.body.innerText;
            const switches = document.querySelectorAll('[role="switch"], input[type="checkbox"]');
            const settings = {};

            // Walk switches and pair them with nearby label text
            switches.forEach((sw, i) => {
                const ariaLabel = sw.getAttribute('aria-label') || '';
                const checked = sw.getAttribute('aria-checked') === 'true' ||
                                sw.checked === true ||
                                sw.getAttribute('data-checked') === 'true';
                // Try to find label text near the switch
                let labelText = ariaLabel;
                if (!labelText) {
                    const parent = sw.closest('div');
                    if (parent) {
                        const text = parent.innerText.trim().split('\\n')[0];
                        labelText = text;
                    }
                }
                settings[`switch_${i}`] = { label: labelText, checked: checked };
            });

            // Also scan the body text for known setting labels
            const lines = body.split('\\n');
            const knownSettings = [
                'Show Instagram badge',
                'Show recent views',
                'Recent views',
            ];
            const found = {};
            knownSettings.forEach(label => {
                const idx = lines.indexOf(label);
                if (idx >= 0) {
                    found[label] = { line_index: idx, next_lines: lines.slice(idx, idx + 3) };
                }
            });

            return { settings, found, raw_lines: lines.slice(0, 50) };
        }""")
        raw = result.get("result", {})

        # Parse the raw data into a clean dict
        settings: dict[str, Any] = {
            "show_instagram_badge": None,
            "show_recent_views": None,
        }

        found = raw.get("found", {})
        switches = raw.get("settings", {})

        # Try to match switches to known settings by label text
        for _idx, info in switches.items():
            label = (info.get("label") or "").strip()
            checked = info.get("checked", False)
            if "instagram" in label.lower() and "badge" in label.lower():
                settings["show_instagram_badge"] = checked
            elif "recent" in label.lower() and "view" in label.lower():
                settings["show_recent_views"] = checked

        # If switch matching failed, try to infer from the found text
        if settings["show_instagram_badge"] is None and "Show Instagram badge" in found:
            settings["show_instagram_badge"] = True  # Present in edit modal
        if settings["show_recent_views"] is None and "Show recent views" in found:
            settings["show_recent_views"] = True

        # Close the Edit profile modal
        try:
            await self.evaluate("""(function() {
                const all = Array.from(document.querySelectorAll('div[role="button"]'));
                const done = all.filter(b => b.innerText.trim() === 'Done').pop();
                if (done) { done.click(); return 'clicked'; }
                const cancel = all.filter(b => b.innerText.trim() === 'Cancel').pop();
                if (cancel) { cancel.click(); return 'clicked'; }
                return 'no close';
            })()""")
            await asyncio.sleep(1)
        except Exception:
            pass

        return settings

    async def update_threads_settings(
        self,
        username: str,
        show_instagram_badge: bool | None = None,
        show_recent_views: bool | None = None,
    ) -> dict[str, Any]:
        """Toggle Threads profile settings via the Edit profile page.

        Threads has no official API for these settings, so we automate
        the web UI through the logged-in browser session.
        """
        await self.navigate(f"https://www.threads.com/@{username}")
        await asyncio.sleep(2)
        # Open the Edit profile modal
        await self.click('div[role="button"]', "Edit profile")
        await asyncio.sleep(2)

        updated: list[str] = []

        toggles = []
        if show_instagram_badge is not None:
            toggles.append(("Show Instagram badge", show_instagram_badge, "show_instagram_badge"))
        if show_recent_views is not None:
            toggles.append(("Show recent views", show_recent_views, "show_recent_views"))

        for label_text, desired, field_name in toggles:
            try:
                result = await self.evaluate(f"""(function() {{
                    const body = document.body.innerText;
                    const lines = body.split('\\n');
                    const idx = lines.indexOf('{label_text}');
                    if (idx < 0) return {{ found: false }};

                    // Walk all switches and find the one closest to the label
                    const switches = Array.from(document.querySelectorAll(
                        '[role="switch"], input[type="checkbox"]'
                    ));
                    if (switches.length === 0) return {{ found: true, switches: 0 }};

                    // Find the switch whose position is closest to the label text
                    let best = null;
                    let bestDist = Infinity;
                    const allElements = Array.from(document.querySelectorAll('*'));
                    const labelEl = allElements.find(e =>
                        e.children.length === 0 && e.textContent.trim() === '{label_text}'
                    );

                    if (labelEl) {{
                        const labelRect = labelEl.getBoundingClientRect();
                        switches.forEach(sw => {{
                            const r = sw.getBoundingClientRect();
                            const dist = Math.abs(r.top - labelRect.top);
                            if (dist < bestDist) {{
                                bestDist = dist;
                                best = sw;
                            }}
                        }});
                    }}

                    if (!best) {{
                        // Fallback: just use the first switch
                        best = switches[0];
                    }}

                    const isChecked = best.getAttribute('aria-checked') === 'true' ||
                                       best.checked === true;
                    if (isChecked === {str(desired).lower()}) {{
                        return {{ found: true, already_set: true }};
                    }}

                    best.click();
                    return {{ found: true, clicked: true, was_checked: isChecked }};
                }})()""")
                res = result.get("result", {})
                if res.get("found") and (res.get("clicked") or res.get("already_set")):
                    updated.append(field_name)
                    await asyncio.sleep(1)
            except Exception as e:
                logger.warning("Threads toggle %s failed: %s", field_name, e)

        # Click Done to save
        try:
            await self.evaluate("""(function() {
                const all = Array.from(document.querySelectorAll('div[role="button"]'));
                const done = all.filter(b => b.innerText.trim() === 'Done').pop();
                if (done) { done.click(); return 'clicked'; }
                return 'no Done';
            })()""")
            await asyncio.sleep(2)
        except Exception:
            pass

        return {
            "platform": "threads",
            "status": "updated",
            "updated_fields": updated,
        }

    # ── Personal Facebook Messenger via browser ────────────────────────

    async def get_personal_messenger_conversations(self) -> dict[str, Any]:
        """Read conversation list from facebook.com/messages.

        Navigates to the Messenger inbox and extracts the list of recent
        conversations with names, preview text, and thread URLs.
        Supports both regular (/messages/t/) and E2EE (/messages/e2ee/t/) threads.
        Requires a logged-in Facebook browser session.
        """
        await self.navigate("https://www.facebook.com/messages/")
        await asyncio.sleep(4)

        result = await self.evaluate("""() => {
            const conversations = [];
            const seen = new Set();

            // Match both regular and E2EE conversation links
            const items = document.querySelectorAll(
                'a[href*="/messages/t/"], ' +
                'a[href*="/messages/e2ee/t/"]'
            );

            items.forEach(item => {
                const href = item.getAttribute('href') || '';
                // Skip "New message" and other non-conversation links
                if (href.includes('/messages/new/') || href.includes('/messages/?')) return;
                if (seen.has(href)) return;
                seen.add(href);

                const text = item.innerText || '';
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                if (lines.length === 0) return;

                // Extract thread ID from URL (both /messages/t/ and /messages/e2ee/t/)
                const match = href.match(/messages\\/(?:e2ee\\/)?t\\/([0-9]+)/);
                const threadId = match ? match[1] : null;
                const isE2EE = href.includes('/e2ee/');

                // First line is the name; skip "Active now" status indicators
                let name = lines[0] || 'Unknown';
                if (name === 'Active now' && lines.length > 1) {
                    name = lines[1] || 'Unknown';
                }

                // Preview is the next non-status line
                let preview = '';
                for (let i = 1; i < lines.length; i++) {
                    const line = lines[i];
                    if (line === 'Active now' || line === '\\u00a0' || line === '\\u00b7') continue;
                    preview = line;
                    break;
                }

                // Unread detection: Facebook uses bold text and/or a blue dot
                const isUnread = item.querySelector('span[style*="font-weight"], span[style*="600"]') !== null ||
                                item.querySelector('[aria-label*="unread"], [aria-label*="Unread"]') !== null ||
                                item.querySelector('circle[fill], [style*="background-color: var(--accent)"]') !== null;

                conversations.push({
                    name: name,
                    preview: preview,
                    thread_id: threadId,
                    url: href,
                    unread: isUnread,
                    e2ee: isE2EE,
                });
            });

            return { conversations, count: conversations.length };
        }""")
        raw = result.get("result", {})
        return {
            "conversations": raw.get("conversations", []),
            "count": raw.get("count", 0),
        }

    async def _navigate_to_thread(self, thread_id: str, is_e2ee: bool = False) -> None:
        """Navigate to a specific Messenger thread.

        Facebook's Messenger SPA intercepts URL changes and redirects to a
        random conversation. To work around this, we navigate to about:blank
        first to unload the SPA, then navigate to the thread URL. This forces
        a fresh SPA load that respects the requested URL.

        For E2EE threads, also handles the PIN entry dialog if it appears.
        """
        path = "e2ee/t" if is_e2ee else "t"
        target_url = f"https://www.facebook.com/messages/{path}/{thread_id}/"

        # Check if we're already on the right thread
        current = await self.evaluate("() => window.location.href")
        current_url = current.get("result", "")
        if current_url == target_url:
            return  # Already on the right thread

        # Step 1: Navigate to about:blank to unload the SPA
        await self.navigate("about:blank")
        await asyncio.sleep(1)

        # Step 2: Navigate to the thread URL (fresh SPA load)
        await self.navigate(target_url)
        await asyncio.sleep(5)  # Wait for SPA to load the thread

        # Step 3: For E2EE threads, handle PIN entry dialog if it appears
        if is_e2ee:
            await self._handle_e2ee_pin_dialog()

    async def _handle_e2ee_pin_dialog(self, pin: str | None = None) -> bool:
        """Handle the E2EE PIN entry dialog if it appears.

        Facebook Messenger shows a PIN entry dialog when opening an E2EE
        conversation for the first time (or after clearing browser data).
        This method detects the dialog and enters the PIN if provided.

        Args:
            pin: The 6-digit PIN for E2EE conversations. If None, checks for
                 the dialog but cannot enter the PIN.

        Returns:
            True if the dialog was found and handled, False if no dialog.
        """
        try:
            result = await self.evaluate("""() => {
                // Look for the E2EE PIN entry dialog
                const dialog = document.querySelector(
                    'div[role="dialog"], ' +
                    'div[aria-label*="PIN"], ' +
                    'div[aria-label*="pin"], ' +
                    'div:has(input[type="password"][placeholder*="PIN"]), ' +
                    'div:has(input[type="password"][placeholder*="pin"])'
                );
                if (!dialog) return { found: false };

                // Check for PIN input
                const pinInput = dialog.querySelector(
                    'input[type="password"], ' +
                    'input[placeholder*="PIN"], ' +
                    'input[placeholder*="pin"], ' +
                    'input[autocomplete="off"][maxlength="6"]'
                );

                // Check for "Enter PIN" or similar text
                const dialogText = dialog.innerText || '';
                const hasPinPrompt = dialogText.includes('PIN') || dialogText.includes('pin');

                return {
                    found: true,
                    hasPinInput: !!pinInput,
                    hasPinPrompt: hasPinPrompt,
                    dialogText: dialogText.substring(0, 200),
                };
            }""")
            data = result.get("result", {})
            if not data.get("found"):
                return False

            if data.get("hasPinInput") and pin:
                # Enter the PIN using the fill endpoint
                await self.fill('div[role="dialog"] input[type="password"]', pin)
                await asyncio.sleep(0.5)
                # Click the submit/continue button
                submit_selector = (
                    'div[role="dialog"] button[type="submit"], '
                    'div[role="dialog"] button:has-text("Continue"), '
                    'div[role="dialog"] button:has-text("Submit")'
                )
                await self.click(submit_selector)
                await asyncio.sleep(2)
                logger.info("E2EE PIN entered successfully")
                return True
            elif data.get("hasPinPrompt") and not pin:
                logger.warning(
                    "E2EE PIN dialog detected but no PIN provided — "
                    "set MESSENGER_E2EE_PIN env var or pass pin parameter"
                )
                return True  # Dialog found but can't enter PIN
            return False
        except Exception as exc:
            logger.debug("E2EE PIN dialog check failed (non-fatal): %s", exc)
            return False

    async def get_personal_messenger_messages(self, thread_id: str, is_e2ee: bool = False) -> dict[str, Any]:
        """Read messages from a specific conversation thread.

        Navigates to the thread URL and extracts all visible messages.
        Supports both regular and E2EE threads.
        """
        await self._navigate_to_thread(thread_id, is_e2ee=is_e2ee)
        await asyncio.sleep(2)  # Extra time for messages to render

        result = await self.evaluate("""() => {
            const messages = [];

            // Messages are in div[data-scope="messages_table"] containers
            const tables = document.querySelectorAll('div[data-scope="messages_table"]');

            tables.forEach(table => {
                const text = (table.innerText || '').trim();
                if (!text) return;

                // Skip timestamp-only entries (e.g. "Jul 14, 2026, 12:10 PM")
                // These have childCount === 1 and only contain a date/time
                if (table.children.length === 1) {
                    const childText = (table.children[0].innerText || '').trim();
                    // Check if it looks like a timestamp
                    if (/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Today|Yesterday|\\d{1,2}:\\d{2})/.test(childText)) {
                        return;
                    }
                }

                // Filter out "Enter, Message sent..." UI artifacts
                let cleanText = text;
                const noisePatterns = [
                    /^Enter,\\s*Message sent.*$/m,
                    /^Enter,?$/m,
                ];
                noisePatterns.forEach(pattern => {
                    cleanText = cleanText.replace(pattern, '').trim();
                });

                // Remove duplicate URL lines (Facebook shows both raw URL and preview)
                const lines = cleanText.split('\\n').filter(l => l.trim());
                const deduped = [];
                const seenLines = new Set();
                lines.forEach(line => {
                    const trimmed = line.trim();
                    if (!seenLines.has(trimmed)) {
                        seenLines.add(trimmed);
                        deduped.push(trimmed);
                    }
                });
                cleanText = deduped.join('\\n').trim();

                if (!cleanText) return;

                // Determine sender: check parent container alignment
                // Outgoing messages are right-aligned, incoming are left-aligned
                const parent = table.parentElement;
                const gp = parent ? parent.parentElement : null;
                const isOutgoing = false; // TODO: detect via DOM when we have outgoing msgs

                // Try to find a timestamp element
                const timeEl = table.querySelector('time, [data-absolute-time], [datetime]');
                let timestamp = null;
                if (timeEl) {
                    timestamp = timeEl.getAttribute('datetime') ||
                               timeEl.getAttribute('data-absolute-time') ||
                               timeEl.innerText;
                }

                messages.push({
                    text: cleanText,
                    sender: isOutgoing ? 'me' : 'them',
                    timestamp: timestamp,
                });
            });

            return { messages, count: messages.length };
        }""")
        raw = result.get("result", {})
        return {
            "messages": raw.get("messages", []),
            "count": raw.get("count", 0),
        }

    async def send_personal_messenger_message(self, thread_id: str, text: str, is_e2ee: bool = False) -> dict[str, Any]:
        """Send a message in a personal Facebook Messenger conversation.

        Navigates to the thread, types in the message input, and presses Enter.
        Supports both regular and E2EE threads.
        """
        await self._navigate_to_thread(thread_id, is_e2ee=is_e2ee)
        await asyncio.sleep(2)  # Extra time for input to render

        # Find the message input box and type using modern input events
        await self.evaluate(f"""(function() {{
            // Facebook Messenger message input — contenteditable div
            const input = document.querySelector(
                '[contenteditable="true"][role="textbox"], ' +
                'div[role="textbox"][contenteditable], ' +
                '[data-contents="true"][contenteditable], ' +
                'div[contenteditable="true"][data-contents="true"]'
            );
            if (!input) return {{ found: false, error: 'Input not found' }};

            input.focus();

            // Method 1: Try execCommand (works in most browsers)
            const inserted = document.execCommand('insertText', false, {json.dumps(text)});

            // Method 2: If execCommand failed, use InputEvent (modern approach)
            if (!inserted) {{
                const inputData = new InputEvent('beforeinput', {{
                    inputType: 'insertText',
                    data: {json.dumps(text)},
                    bubbles: true,
                    cancelable: true,
                }});
                input.dispatchEvent(inputData);
            }}

            return {{ found: true, inserted: inserted }};
        }})()""")

        await asyncio.sleep(1)

        # Press Enter to send using modern KeyboardEvent
        result = await self.evaluate("""(function() {
            const input = document.querySelector(
                '[contenteditable="true"][role="textbox"], ' +
                'div[role="textbox"][contenteditable], ' +
                '[data-contents="true"][contenteditable], ' +
                'div[contenteditable="true"][data-contents="true"]'
            );
            if (!input) return { found: false, sent: false };

            input.focus();

            // Simulate Enter keypress with modern KeyboardEvent
            const enterEvent = new KeyboardEvent('keydown', {
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13,
                bubbles: true,
                cancelable: true,
            });
            input.dispatchEvent(enterEvent);

            // Also try pressing the send button as fallback
            const sendBtn = document.querySelector(
                '[aria-label="Send"], [aria-label="Press Enter to send"], ' +
                'div[role="button"][aria-label*="Send"]'
            );
            if (sendBtn) sendBtn.click();

            return { found: true, sent: true };
        })()""")

        await asyncio.sleep(2)
        raw = result.get("result", {})
        return {
            "status": "sent" if raw.get("sent") else "failed",
            "thread_id": thread_id,
            "message": raw,
        }

    async def trigger_typing_indicator(self, thread_id: str, is_e2ee: bool = False, duration: float = 2.0) -> None:
        """Simulate a typing indicator by focusing the input and waiting.

        Facebook Messenger shows "X is typing..." when the input is focused.
        This makes the bot feel more natural and follows Meta's "Be Predictable"
        best practice — users expect a brief pause before a reply.
        """
        try:
            await self._navigate_to_thread(thread_id, is_e2ee=is_e2ee)
            await self.evaluate("""(function() {
                const input = document.querySelector(
                    '[contenteditable="true"][role="textbox"], ' +
                    'div[role="textbox"][contenteditable], ' +
                    '[data-contents="true"][contenteditable]'
                );
                if (input) input.focus();
            })()""")
            await asyncio.sleep(duration)
        except Exception as exc:
            logger.debug("Typing indicator failed (non-fatal): %s", exc)

    # ── Cookie-based fast reads (mobile/basic HTML) ──────────────────────

    async def get_personal_messenger_conversations_fast(self) -> dict[str, Any]:
        """Fast conversation list read via m.facebook.com basic HTML.

        Uses the mobile basic version of Facebook which loads much faster
        than the full SPA at facebook.com/messages. The basic HTML version
        renders conversation list server-side, eliminating the need for
        client-side SPA hydration (saves ~3-4 seconds per read).

        Falls back to the full SPA method if the basic version is unavailable.
        """
        try:
            # Navigate to mobile basic messages — much lighter than full SPA
            await self.navigate("https://m.facebook.com/messages")
            await asyncio.sleep(2)  # Basic HTML loads fast

            result = await self.evaluate("""() => {
                const conversations = [];
                const seen = new Set();

                // m.facebook.com uses simpler anchor tags with /messages/t/ or /messages/e2ee/t/
                const items = document.querySelectorAll(
                    'a[href*="/messages/t/"], ' +
                    'a[href*="/messages/e2ee/t/"]'
                );

                items.forEach(item => {
                    const href = item.getAttribute('href') || '';
                    if (href.includes('/messages/new/') || seen.has(href)) return;
                    seen.add(href);

                    const text = (item.innerText || '').trim();
                    if (!text) return;

                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                    if (lines.length === 0) return;

                    const match = href.match(/messages\\/(?:e2ee\\/)?t\\/([0-9]+)/);
                    const threadId = match ? match[1] : null;
                    const isE2EE = href.includes('/e2ee/');

                    const name = lines[0] || 'Unknown';
                    const preview = lines.length > 1 ? lines[lines.length - 1] : '';

                    // Basic HTML marks unread with bold text or a different background
                    const isUnread = item.querySelector('strong, b') !== null ||
                                    (item.style && item.style.fontWeight === 'bold');

                    conversations.push({
                        name: name,
                        preview: preview,
                        thread_id: threadId,
                        url: href.startsWith('http') ? href : 'https://m.facebook.com' + href,
                        unread: isUnread,
                        e2ee: isE2EE,
                    });
                });

                return { conversations, count: conversations.length };
            }""")
            raw = result.get("result", {})
            convos = raw.get("conversations", [])
            if convos:
                return {
                    "conversations": convos,
                    "count": raw.get("count", 0),
                    "source": "mobile_basic",
                }
            # Fall back to full SPA if basic returned nothing
            logger.info("Mobile basic returned no conversations, falling back to full SPA")
        except Exception as exc:
            logger.warning("Fast conversation read failed, falling back to SPA: %s", exc)

        return await self.get_personal_messenger_conversations()

    async def get_personal_messenger_messages_fast(self, thread_id: str, is_e2ee: bool = False) -> dict[str, Any]:
        """Fast message read via m.facebook.com basic HTML.

        Reads messages from a specific thread using the mobile basic version.
        Much faster than the full SPA (~2s vs ~7s) since it renders server-side.

        Falls back to the full SPA method if the basic version fails.
        """
        try:
            path = "e2ee/t" if is_e2ee else "t"
            await self.navigate(f"https://m.facebook.com/messages/{path}/{thread_id}/")
            await asyncio.sleep(2)

            result = await self.evaluate("""() => {
                const messages = [];

                // m.facebook.com basic HTML uses simpler message containers
                // Messages are in div elements with data-scope or in table rows
                const containers = document.querySelectorAll(
                    'div[data-scope="messages_table"], ' +
                    'div[role="article"], ' +
                    'div.message, ' +
                    'table tbody tr'
                );

                containers.forEach(container => {
                    const text = (container.innerText || '').trim();
                    if (!text) return;

                    // Skip timestamp-only entries
                    if (container.children.length === 1) {
                        const childText = (container.children[0].innerText || '').trim();
                        if (/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Today|Yesterday|\\d{1,2}:\\d{2})/.test(childText)) {
                            return;
                        }
                    }

                    // Clean noise
                    let cleanText = text.replace(/^Enter,?\\s*Message sent.*$/m, '').trim();
                    if (!cleanText) return;

                    const timeEl = container.querySelector('time, [data-absolute-time], [datetime], abbr');
                    let timestamp = null;
                    if (timeEl) {
                        timestamp = timeEl.getAttribute('datetime') ||
                                   timeEl.getAttribute('data-absolute-time') ||
                                   timeEl.innerText;
                    }

                    // In basic HTML, outgoing messages often have a different class
                    const isOutgoing = container.classList.contains('outgoing') ||
                                     container.closest('[class*="outgoing"]') !== null ||
                                     container.closest('[class*="sent"]') !== null;

                    messages.push({
                        text: cleanText,
                        sender: isOutgoing ? 'me' : 'them',
                        timestamp: timestamp,
                    });
                });

                return { messages, count: messages.length };
            }""")
            raw = result.get("result", {})
            msgs = raw.get("messages", [])
            if msgs:
                return {
                    "messages": msgs,
                    "count": raw.get("count", 0),
                    "source": "mobile_basic",
                }
            logger.info("Fast message read returned no messages, falling back to full SPA")
        except Exception as exc:
            logger.warning("Fast message read failed, falling back to SPA: %s", exc)

        return await self.get_personal_messenger_messages(thread_id, is_e2ee=is_e2ee)

    # ── Threads DM (direct messages) ──────────────────────────────────────
    # Threads launched DMs in July 2025 but has no public DM API.
    # These methods automate the Threads web UI (threads.com/direct)
    # using the same browser bridge approach as personal Messenger.

    async def get_threads_dm_conversations(self) -> dict[str, Any]:
        """Read Threads DM conversation list from threads.com/direct.

        Navigates to the Threads inbox and extracts recent conversations
        with names, preview text, and thread URLs.
        Requires a logged-in Threads browser session.
        """
        await self.navigate("https://www.threads.com/direct/inbox/")
        await asyncio.sleep(4)

        response = await self.evaluate("""() => {
            const conversations = [];
            const seen = new Set();

            // Threads DM links contain /direct/t/ or /direct/thread/
            const items = document.querySelectorAll(
                'a[href*="/direct/t/"], ' +
                'a[href*="/direct/thread/"], ' +
                'a[href*="/direct/inbox/"]'
            );

            items.forEach(item => {
                const href = item.getAttribute('href') || '';
                if (href.includes('/direct/new') || href.includes('/direct/?')) return;
                if (seen.has(href)) return;
                seen.add(href);

                const text = item.innerText || '';
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                if (lines.length === 0) return;

                // Extract thread ID from URL
                const match = href.match(/direct\\/(?:t|thread|inbox)\\/([0-9a-zA-Z_-]+)/);
                const threadId = match ? match[1] : null;

                const name = lines[0] || 'Unknown';
                let preview = '';
                for (let i = 1; i < lines.length; i++) {
                    const line = lines[i];
                    if (line === 'Active now' || line === '\\u00a0') continue;
                    preview = line;
                    break;
                }

                // Check for unread indicator
                const unreadEl = item.querySelector('[class*="unread"], [class*="badge"], [data-scope="unread_count"]');
                const unread = unreadEl ? unreadEl.innerText.trim() : '';

                conversations.push({
                    name: name,
                    preview: preview,
                    thread_id: threadId,
                    thread_url: href,
                    unread: unread,
                });
            });

            return { conversations: conversations, count: conversations.length };
        }""")

        return response.get("result", response) if isinstance(response, dict) else response

    async def get_threads_dm_messages(self, thread_id: str) -> dict[str, Any]:
        """Read messages in a Threads DM thread.

        Navigates to the specific DM thread and extracts all visible messages
        with sender names, text, and timestamps.
        """
        await self.navigate(f"https://www.threads.com/direct/t/{thread_id}/")
        await asyncio.sleep(4)

        # Scroll up to load older messages
        await self.evaluate("""() => {
            const container = document.querySelector('[class*="message-list"], [role="log"], [class*="chat"]');
            if (container) container.scrollTop = 0;
        }""")
        await asyncio.sleep(1)

        response = await self.evaluate("""() => {
            const messages = [];

            // Threads DM messages are in various container patterns
            const msgEls = document.querySelectorAll(
                '[class*="message-item"], ' +
                '[class*="msg-item"], ' +
                '[data-scope="message"], ' +
                'div[role="article"]'
            );

            let currentSender = '';
            msgEls.forEach(el => {
                const senderEl = el.querySelector('[class*="sender"], [class*="author"], h3, h4, [class*="name"]');
                const sender = senderEl ? senderEl.innerText.trim() : currentSender;
                if (sender) currentSender = sender;

                const textEl = el.querySelector('[class*="message-text"], p, [class*="content"]');
                const text = textEl ? textEl.innerText.trim() : el.innerText.trim();

                const timeEl = el.querySelector('time, [class*="time"], [class*="timestamp"]');
                const time = timeEl ? timeEl.innerText.trim() : '';

                if (text && text.length < 2000) {
                    messages.push({ sender: sender || 'unknown', text: text, time: time });
                }
            });

            return { thread_id: arguments[0], messages: messages, count: messages.length };
        }""")

        # Extract the result from the browser bridge response
        result = response.get("result", response) if isinstance(response, dict) else response
        # The evaluate doesn't pass arguments well, fix thread_id
        if isinstance(result, dict):
            result["thread_id"] = thread_id

        return result

    async def send_threads_dm_message(self, thread_id: str, text: str) -> dict[str, Any]:
        """Send a message in a Threads DM thread.

        Navigates to the thread, types the message, and sends it.
        """
        await self.navigate(f"https://www.threads.com/direct/t/{thread_id}/")
        await asyncio.sleep(4)

        # Find the message input (contenteditable or textarea)
        # Escape the text for safe JS embedding
        import json as _json
        escaped_text = _json.dumps(text)
        type_response = await self.evaluate(f"""() => {{
            const editor = document.querySelector(
                'div[contenteditable="true"][role="textbox"], ' +
                'textarea[class*="message"], ' +
                'textarea[placeholder*="essage"], ' +
                '[data-scope="message_input"]'
            );
            if (!editor) return {{ error: 'Could not find the message input box' }};

            // Focus and type
            editor.focus();

            // Use execCommand for contenteditable
            if (editor.isContentEditable) {{
                document.execCommand('insertText', false, {escaped_text});
            }} else {{
                editor.value = {escaped_text};
                editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}

            return {{ status: 'typed' }};
        }}""")

        result = type_response.get("result", type_response) if isinstance(type_response, dict) else type_response
        if isinstance(result, dict) and result.get("error"):
            return result

        await asyncio.sleep(1)

        # Click Send button or press Enter
        send_response = await self.evaluate("""() => {
            const sendBtn = document.querySelector(
                'button[type="submit"], ' +
                'button[aria-label*="Send"]'
            );
            if (sendBtn) {
                sendBtn.click();
                return { status: 'sent', method: 'button' };
            }
            return { status: 'no_button' };
        }""")

        send_result = send_response.get("result", send_response) if isinstance(send_response, dict) else send_response
        if isinstance(send_result, dict) and send_result.get("status") == "no_button":
            # Try pressing Enter
            await self.evaluate("""() => {
                const editor = document.querySelector('div[contenteditable="true"][role="textbox"], textarea[class*="message"]');
                if (editor) {
                    editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                }
            }""")

        await asyncio.sleep(2)

        return {"status": "ok", "sent": True, "thread_id": thread_id, "text": text}

    # ── Twitter/X DM (direct messages) ───────────────────────────────────
    # Twitter/X has a DM API v2 but it requires a paid tier ($200/mo Basic,
    # $5000/mo Pro). Free tier can't read or send DMs via API.
    # These methods automate the Twitter/X web UI (x.com/messages) using
    # the same browser bridge approach as personal Messenger and Threads.
    # Based on open-source projects: x-use, tweetly, x-mcp-bridge.

    async def get_twitter_dm_conversations(self) -> dict[str, Any]:
        """Read Twitter/X DM conversation list from x.com/messages.

        Navigates to the Twitter/X inbox and extracts recent conversations
        with names, preview text, and thread URLs.
        Requires a logged-in Twitter/X browser session.
        """
        await self.navigate("https://x.com/messages")
        await asyncio.sleep(4)

        response = await self.evaluate("""() => {
            const conversations = [];
            const seen = new Set();

            // Twitter/X DM conversation links
            const items = document.querySelectorAll(
                'a[href*="/messages/"], ' +
                'div[data-testid="conversation"], ' +
                'div[role="link"][aria-label]'
            );

            items.forEach(item => {
                const href = item.getAttribute('href') || '';
                if (!href.includes('/messages/') || href.includes('/messages/compose')) return;
                if (seen.has(href)) return;
                seen.add(href);

                const text = item.innerText || '';
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                if (lines.length === 0) return;

                // Extract conversation ID from URL
                const match = href.match(/messages\\/([0-9]+)/);
                const threadId = match ? match[1] : null;

                const name = lines[0] || 'Unknown';
                let preview = '';
                for (let i = 1; i < lines.length; i++) {
                    const line = lines[i];
                    if (line === 'Active now' || line === '\\u00a0') continue;
                    preview = line;
                    break;
                }

                // Check for unread indicator
                const unreadEl = item.querySelector('[class*="unread"], [class*="badge"], [data-testid="unreadIndicator"]');
                const unread = unreadEl ? unreadEl.innerText.trim() : '';

                conversations.push({
                    name: name,
                    preview: preview,
                    thread_id: threadId,
                    thread_url: href,
                    unread: unread,
                });
            });

            return { conversations: conversations, count: conversations.length };
        }""")

        return response.get("result", response) if isinstance(response, dict) else response

    async def get_twitter_dm_messages(self, thread_id: str) -> dict[str, Any]:
        """Read messages in a Twitter/X DM thread.

        Navigates to the specific DM thread and extracts all visible messages
        with sender names, text, and timestamps.
        """
        await self.navigate(f"https://x.com/messages/{thread_id}")
        await asyncio.sleep(4)

        # Scroll up to load older messages
        await self.evaluate("""() => {
            const container = document.querySelector(
                '[class*="message-list"], [role="log"], [data-testid="conversation"]'
            );
            if (container) container.scrollTop = 0;
        }""")
        await asyncio.sleep(1)

        response = await self.evaluate("""() => {
            const messages = [];

            // Twitter/X DM messages are in various container patterns
            const msgEls = document.querySelectorAll(
                '[data-testid="messageEntry"], ' +
                '[class*="message-item"], ' +
                'div[role="article"]'
            );

            let currentSender = '';
            msgEls.forEach(el => {
                const senderEl = el.querySelector('[class*="sender"], [class*="author"], [data-testid="UserAvatar"]');
                const sender = senderEl ? (senderEl.getAttribute('aria-label') || senderEl.innerText || '').trim() : currentSender;
                if (sender) currentSender = sender;

                const textEl = el.querySelector('[data-testid="messageText"], [class*="message-text"], [dir="auto"]');
                const text = textEl ? textEl.innerText.trim() : el.innerText.trim();

                const timeEl = el.querySelector('time, [class*="time"], [class*="timestamp"]');
                const time = timeEl ? timeEl.innerText.trim() : '';

                if (text && text.length < 2000) {
                    messages.push({ sender: sender || 'unknown', text: text, time: time });
                }
            });

            return { messages: messages, count: messages.length };
        }""")

        result = response.get("result", response) if isinstance(response, dict) else response
        if isinstance(result, dict):
            result["thread_id"] = thread_id

        return result

    async def send_twitter_dm_message(self, thread_id: str, text: str) -> dict[str, Any]:
        """Send a message in a Twitter/X DM thread.

        Navigates to the thread, types the message, and sends it.
        """
        await self.navigate(f"https://x.com/messages/{thread_id}")
        await asyncio.sleep(4)

        import json as _json
        escaped_text = _json.dumps(text)
        type_response = await self.evaluate(f"""() => {{
            const editor = document.querySelector(
                'div[contenteditable="true"][data-testid="tweetTextarea_0"], ' +
                'div[contenteditable="true"][role="textbox"], ' +
                'textarea[placeholder*="essage"], ' +
                'textarea[placeholder*="Start a message"]'
            );
            if (!editor) return {{ error: 'Could not find the message input box' }};

            editor.focus();

            if (editor.isContentEditable) {{
                document.execCommand('insertText', false, {escaped_text});
            }} else {{
                editor.value = {escaped_text};
                editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}

            return {{ status: 'typed' }};
        }}""")

        result = type_response.get("result", type_response) if isinstance(type_response, dict) else type_response
        if isinstance(result, dict) and result.get("error"):
            return result

        await asyncio.sleep(1)

        # Click Send button or press Enter
        send_response = await self.evaluate("""() => {
            const sendBtn = document.querySelector(
                'button[data-testid="dmSendButton"], ' +
                'button[aria-label*="Send"]'
            );
            if (sendBtn) {
                sendBtn.click();
                return { status: 'sent', method: 'button' };
            }
            return { status: 'no_button' };
        }""")

        send_result = send_response.get("result", send_response) if isinstance(send_response, dict) else send_response
        if isinstance(send_result, dict) and send_result.get("status") == "no_button":
            await self.evaluate("""() => {
                const editor = document.querySelector('div[contenteditable="true"][role="textbox"], textarea');
                if (editor) {
                    editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                }
            }""")

        await asyncio.sleep(2)

        return {"status": "ok", "sent": True, "thread_id": thread_id, "text": text}

    async def post_tweet(
        self, text: str, image_paths: list[str] | None = None
    ) -> dict[str, Any]:
        """Post a tweet via the x.com web composer (free fallback for the
        paid X API — used when POST /2/tweets returns 402 credits-depleted).

        ``image_paths`` are host-visible media paths under ``/app/uploads``
        (browser-novnc mounts the same uploads dir) and are attached via the
        bridge's native ``set_input_files`` — synthetic DataTransfer
        injection leaves X's upload spinner stuck forever.
        """
        session = await self.ensure_session("twitter")
        if session.get("status") != "active":
            return {"status": "error", "error": session.get("message", "Twitter browser session not active"), **session}

        await self.navigate("https://x.com/compose/post")

        import json as _json

        # The SPA renders the composer asynchronously — wait for it (up to ~15s).
        editor_found = False
        for _ in range(15):
            await asyncio.sleep(1)
            probe = await self.evaluate("""() => ({
                editor: !!document.querySelector(
                    'div[contenteditable="true"][data-testid="tweetTextarea_0"], ' +
                    'div[contenteditable="true"][role="textbox"]'
                ),
                url: location.href
            })""")
            pr = probe.get("result", probe) if isinstance(probe, dict) else probe
            if isinstance(pr, dict) and pr.get("editor"):
                editor_found = True
                break
        if not editor_found:
            landed = pr.get("url") if isinstance(pr, dict) else None
            return {
                "status": "error",
                "error": (
                    f"Could not find the tweet composer (landed on {landed}) — "
                    "the x.com browser session is likely logged out; "
                    "re-login via the noVNC viewer (/session/start twitter)"
                ),
            }

        escaped_text = _json.dumps(text)
        type_response = await self.evaluate(f"""() => {{
            const editor = document.querySelector(
                'div[contenteditable="true"][data-testid="tweetTextarea_0"], ' +
                'div[contenteditable="true"][role="textbox"]'
            );
            if (!editor) return {{ error: 'Could not find the tweet composer' }};
            editor.focus();
            // Clear any persisted draft first so we never double-type.
            document.execCommand('selectAll', false, null);
            document.execCommand('delete', false, null);
            document.execCommand('insertText', false, {escaped_text});
            return {{ status: 'typed', length: (editor.innerText || '').length }};
        }}""")
        result = type_response.get("result", type_response) if isinstance(type_response, dict) else type_response
        if isinstance(result, dict) and result.get("error"):
            return {"status": "error", "error": result["error"]}

        # Remove attachments left over from a persisted draft so the image
        # is not attached twice.
        await self.evaluate("""() => {
            const box = document.querySelector('[data-testid="attachments"]');
            if (!box) return { status: 'no_attachments' };
            const rm = box.querySelector('button[aria-label*="Remove"], button[aria-label*="remove"], div[role="button"][aria-label*="Remove"]');
            if (rm) rm.click();
            return { status: 'cleared' };
        }""")
        await asyncio.sleep(1)

        # Attach images through the composer's hidden file input using the
        # bridge's native set_input_files (trusted upload that completes).
        for path in (image_paths or [])[:4]:
            try:
                await self.upload_file('input[data-testid="fileInput"]', path)
            except BrowserBridgeError as exc:
                return {"status": "error", "error": f"media attach failed: {exc}"}
            # Wait for the media thumbnail/upload to finish (up to ~30s)
            for _ in range(30):
                await asyncio.sleep(1)
                up = await self.evaluate("""() => ({
                    uploading: !!document.querySelector('[role="progressbar"]'),
                    preview: !!document.querySelector('[data-testid="attachments"] img, [data-testid="attachments"] video')
                })""")
                ur = up.get("result", up) if isinstance(up, dict) else up
                if isinstance(ur, dict) and not ur.get("uploading") and ur.get("preview"):
                    break

        # The Post button stays disabled until React registers input state —
        # poll for it to become enabled (up to ~10s). If it never does, fall
        # back to X's native Ctrl/Cmd+Enter composer shortcut.
        clicked = False
        last_err = "Post button not found"
        for _ in range(10):
            await asyncio.sleep(1)
            post_response = await self.evaluate("""() => {
                const btns = [...document.querySelectorAll(
                    'button[data-testid="tweetButton"], ' +
                    'button[data-testid="tweetButtonInline"]'
                )];
                if (!btns.length) return { error: 'Post button not found' };
                const btn = btns.find(b => !b.disabled && b.getAttribute('aria-disabled') !== 'true');
                if (!btn) return { error: 'Post button disabled' };
                btn.click();
                return { status: 'clicked' };
            }""")
            pr = post_response.get("result", post_response) if isinstance(post_response, dict) else post_response
            if isinstance(pr, dict):
                if pr.get("status") == "clicked":
                    clicked = True
                    break
                last_err = pr.get("error") or last_err
        if not clicked:
            # Ctrl+Enter is X's native "post" shortcut — works even when the
            # button's disabled flag didn't update from synthetic typing.
            await self.evaluate("""() => {
                const editor = document.querySelector(
                    'div[contenteditable="true"][data-testid="tweetTextarea_0"], ' +
                    'div[contenteditable="true"][role="textbox"]'
                );
                if (editor) {
                    editor.focus();
                    editor.dispatchEvent(new KeyboardEvent('keydown',
                        { key: 'Enter', code: 'Enter', ctrlKey: true, bubbles: true }));
                }
            }""")
            await asyncio.sleep(3)

        await asyncio.sleep(3)

        # X sometimes shows a confirmation nudge (e.g. "Want to review this
        # before posting?") — click through it if present.
        await self.evaluate("""() => {
            const btn = document.querySelector(
                'button[data-testid="tweetButton"], ' +
                'button[data-testid="tweetButtonInline"], ' +
                'button[data-testid="confirmationSheetConfirm"]'
            );
            if (btn && !btn.disabled && btn.getAttribute('aria-disabled') !== 'true') btn.click();
        }""")

        await asyncio.sleep(4)

        # Composer closes on success — confirm the compose *modal* is gone.
        # (x.com/home always has an inline tweetTextarea_0, so only count
        # editors inside a dialog, or when still on the /compose URL.)
        verify = await self.evaluate("""() => {
            const inDialog = !!document.querySelector('[role="dialog"] div[data-testid="tweetTextarea_0"]');
            const onCompose = location.pathname.startsWith('/compose');
            return { composerOpen: inDialog || onCompose, url: location.href };
        }""")
        vr = verify.get("result", verify) if isinstance(verify, dict) else verify
        if isinstance(vr, dict) and vr.get("composerOpen"):
            return {"status": "error", "error": "Composer still open after Post click — tweet likely not sent"}

        # Grab the posted tweet URL for the record. X shows a "Your post was
        # sent — View" toast whose link points at the new status; fall back to
        # the profile page's first status link (the home timeline's top
        # article can belong to a followed account — never scrape /home).
        link_resp = await self.evaluate("""() => {
            const toast = document.querySelector('[data-testid="toast"] a[href*="/status/"]');
            const acct = document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]');
            const m = acct ? (acct.getAttribute('aria-label') || '').match(/@([A-Za-z0-9_]+)/) : null;
            return { url: toast ? toast.href : null, handle: m ? m[1] : null };
        }""")
        lr = link_resp.get("result", link_resp) if isinstance(link_resp, dict) else link_resp
        tweet_url = lr.get("url") if isinstance(lr, dict) else None
        handle = lr.get("handle") if isinstance(lr, dict) else None
        if not tweet_url and handle:
            await self.navigate(f"https://x.com/{handle}")
            await asyncio.sleep(4)
            link_resp = await self.evaluate("""() => {
                const a = document.querySelector('article a[href*="/status/"] time')?.closest('a');
                return { url: a ? a.href : null };
            }""")
            lr = link_resp.get("result", link_resp) if isinstance(link_resp, dict) else link_resp
            tweet_url = lr.get("url") if isinstance(lr, dict) else None
        return {"status": "ok", "posted": True, "url": tweet_url}

    # ── TikTok DM (direct messages) ─────────────────────────────────────
    # TikTok's Business Messaging API is in Open Beta (APAC, LATAM, METAP,
    # NA) but not available in EU. These methods automate the TikTok web UI
    # (tiktok.com/messages) using browser automation.
    # Based on open-source projects: TikTokStreakSaver, tikbot, tiktok_dm.

    async def get_tiktok_dm_conversations(self) -> dict[str, Any]:
        """Read TikTok DM conversation list from tiktok.com/messages.

        Navigates to the TikTok inbox and extracts recent conversations
        with names, preview text, and thread URLs.
        Requires a logged-in TikTok browser session.
        """
        await self.navigate("https://www.tiktok.com/messages")
        await asyncio.sleep(4)

        response = await self.evaluate("""() => {
            const conversations = [];
            const seen = new Set();

            // TikTok DM conversation items
            const items = document.querySelectorAll(
                'a[href*="/messages/"], ' +
                'div[class*="conversation"], ' +
                'div[class*="chat-item"], ' +
                'div[data-e2e="chat-item"]'
            );

            items.forEach(item => {
                const href = item.getAttribute('href') || '';
                if (href.includes('/messages/compose') || href === '/messages') return;
                if (seen.has(href || item.innerText)) return;
                seen.add(href || item.innerText);

                const text = item.innerText || '';
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                if (lines.length === 0) return;

                // Extract conversation ID from URL or data attribute
                const match = href.match(/messages\\/([0-9a-zA-Z_-]+)/);
                const threadId = match ? match[1] : (item.getAttribute('data-conversation-id') || '');

                const name = lines[0] || 'Unknown';
                let preview = '';
                for (let i = 1; i < lines.length; i++) {
                    const line = lines[i];
                    if (line === 'Active now' || line === '\\u00a0') continue;
                    preview = line;
                    break;
                }

                // Check for unread indicator
                const unreadEl = item.querySelector('[class*="unread"], [class*="badge"], [data-e2e="unread"]');
                const unread = unreadEl ? unreadEl.innerText.trim() : '';

                conversations.push({
                    name: name,
                    preview: preview,
                    thread_id: threadId,
                    thread_url: href,
                    unread: unread,
                });
            });

            return { conversations: conversations, count: conversations.length };
        }""")

        return response.get("result", response) if isinstance(response, dict) else response

    async def get_tiktok_dm_messages(self, thread_id: str) -> dict[str, Any]:
        """Read messages in a TikTok DM thread.

        Navigates to the specific DM thread and extracts all visible messages
        with sender names, text, and timestamps.
        """
        await self.navigate(f"https://www.tiktok.com/messages/{thread_id}")
        await asyncio.sleep(4)

        # Scroll up to load older messages
        await self.evaluate("""() => {
            const container = document.querySelector(
                '[class*="message-list"], [class*="chat-container"], [role="log"]'
            );
            if (container) container.scrollTop = 0;
        }""")
        await asyncio.sleep(1)

        response = await self.evaluate("""() => {
            const messages = [];

            // TikTok DM messages are in various container patterns
            const msgEls = document.querySelectorAll(
                '[class*="message-item"], ' +
                '[class*="msg-item"], ' +
                '[data-e2e="message-item"], ' +
                'div[role="article"]'
            );

            let currentSender = '';
            msgEls.forEach(el => {
                const senderEl = el.querySelector('[class*="sender"], [class*="author"], [class*="name"], [data-e2e="sender"]');
                const sender = senderEl ? senderEl.innerText.trim() : currentSender;
                if (sender) currentSender = sender;

                const textEl = el.querySelector('[class*="message-text"], [data-e2e="message-text"], p, [class*="content"]');
                const text = textEl ? textEl.innerText.trim() : el.innerText.trim();

                const timeEl = el.querySelector('time, [class*="time"], [class*="timestamp"]');
                const time = timeEl ? timeEl.innerText.trim() : '';

                if (text && text.length < 2000) {
                    messages.push({ sender: sender || 'unknown', text: text, time: time });
                }
            });

            return { messages: messages, count: messages.length };
        }""")

        result = response.get("result", response) if isinstance(response, dict) else response
        if isinstance(result, dict):
            result["thread_id"] = thread_id

        return result

    async def send_tiktok_dm_message(self, thread_id: str, text: str) -> dict[str, Any]:
        """Send a message in a TikTok DM thread.

        Navigates to the thread, types the message, and sends it.
        """
        await self.navigate(f"https://www.tiktok.com/messages/{thread_id}")
        await asyncio.sleep(4)

        import json as _json
        escaped_text = _json.dumps(text)
        type_response = await self.evaluate(f"""() => {{
            const editor = document.querySelector(
                'div[contenteditable="true"][role="textbox"], ' +
                'textarea[class*="message"], ' +
                'textarea[placeholder*="essage"], ' +
                'textarea[placeholder*="Send a message"], ' +
                '[data-e2e="message-input"]'
            );
            if (!editor) return {{ error: 'Could not find the message input box' }};

            editor.focus();

            if (editor.isContentEditable) {{
                document.execCommand('insertText', false, {escaped_text});
            }} else {{
                editor.value = {escaped_text};
                editor.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}

            return {{ status: 'typed' }};
        }}""")

        result = type_response.get("result", type_response) if isinstance(type_response, dict) else type_response
        if isinstance(result, dict) and result.get("error"):
            return result

        await asyncio.sleep(1)

        # Click Send button or press Enter
        send_response = await self.evaluate("""() => {
            const sendBtn = document.querySelector(
                'button[type="submit"], ' +
                'button[aria-label*="Send"], ' +
                'button[data-e2e="send-button"]'
            );
            if (sendBtn) {
                sendBtn.click();
                return { status: 'sent', method: 'button' };
            }
            return { status: 'no_button' };
        }""")

        send_result = send_response.get("result", send_response) if isinstance(send_response, dict) else send_response
        if isinstance(send_result, dict) and send_result.get("status") == "no_button":
            await self.evaluate("""() => {
                const editor = document.querySelector('div[contenteditable="true"][role="textbox"], textarea');
                if (editor) {
                    editor.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, which: 13, bubbles: true }));
                }
            }""")

        await asyncio.sleep(2)

        return {"status": "ok", "sent": True, "thread_id": thread_id, "text": text}


    # ── Instagram DM (direct messages) ──────────────────────────────────
    # Instagram's Graph API requires App Review for instagram_business_manage_messages.
    # These methods use the Instagram web API (www.instagram.com/api/v1/) via the
    # browser context, which has the sessionid cookie and can read/send DMs.
    # Requires a logged-in Instagram browser session in browser-novnc.

    async def get_instagram_dm_conversations(self) -> dict[str, Any]:
        """Read Instagram DM conversation list via the web API.

        Uses fetch() from the browser context to call the Instagram web API
        (direct_v2/inbox). Returns conversations with thread IDs and participants.
        Requires a logged-in Instagram browser session.
        """
        await self.navigate("https://www.instagram.com/")
        await asyncio.sleep(3)

        response = await self.evaluate("""async () => {
            try {
                const resp = await fetch(
                    "https://www.instagram.com/api/v1/direct_v2/inbox/?thread_message_limit=10&limit=25",
                    {
                        headers: {"x-ig-app-id": "936619743392459"},
                        credentials: "include"
                    }
                );
                if (!resp.ok) return {error: "HTTP " + resp.status};
                const data = await resp.json();
                const threads = data.inbox?.threads || [];
                const conversations = threads.map(t => {
                    const users = t.users || [];
                    const items = t.items || [];
                    const lastItem = items[items.length - 1] || {};
                    return {
                        id: t.thread_id || "",
                        name: users[0]?.username || "Unknown",
                        participant_id: String(users[0]?.pk || ""),
                        preview: lastItem.text || lastItem.share_text || "",
                        timestamp: t.last_activity_at || "",
                    };
                });
                return {conversations: conversations, count: conversations.length};
            } catch(e) {
                return {error: e.message};
            }
        }""")

        # Extract the result from the browser bridge response
        result = response.get("result", response) if isinstance(response, dict) else response
        return result if isinstance(result, dict) else {"error": str(result)}

    async def get_instagram_dm_messages(self, thread_id: str) -> dict[str, Any]:
        """Read messages in an Instagram DM thread via the web API.

        Uses fetch() from the browser context to call the Instagram web API
        (direct_v2/threads/{thread_id}). Returns messages with sender IDs and text.
        """
        # Navigate to Instagram first — the browser may be on another platform
        # (Facebook, Threads, etc.) if another worker navigated it.
        await self.navigate("https://www.instagram.com/")
        await asyncio.sleep(2)

        response = await self.evaluate(f"""async () => {{
            try {{
                const resp = await fetch(
                    "https://www.instagram.com/api/v1/direct_v2/threads/{thread_id}/",
                    {{
                        headers: {{"x-ig-app-id": "936619743392459"}},
                        credentials: "include"
                    }}
                );
                if (!resp.ok) return {{error: "HTTP " + resp.status}};
                const data = await resp.json();
                const items = data.thread?.items || [];
                const messages = items.map(item => {{
                    return {{
                        id: item.item_id || "",
                        sender_id: String(item.user_id || ""),
                        is_sent_by_viewer: item.is_sent_by_viewer || false,
                        text: item.text || item.share_text || "",
                        timestamp: item.timestamp || "",
                    }};
                }});
                return {{messages: messages, count: messages.length}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        }}""")

        # Extract the result from the browser bridge response
        result = response.get("result", response) if isinstance(response, dict) else response
        return result if isinstance(result, dict) else {"error": str(result)}

    async def send_instagram_dm_message(self, recipient_id: str, text: str) -> dict[str, Any]:
        """Send an Instagram DM via the web UI.

        Instagram's web API POST endpoint (direct_v2/threads/broadcast/text/)
        returns an opaque redirect when called from the browser context, so we
        use UI interaction instead: navigate to the DM thread, type the message
        in the contenteditable editor, and press Enter / click Send.
        This mirrors the approach used for Threads, Twitter, and TikTok.
        """
        import json as _json

        escaped_text = _json.dumps(text)

        # 1. Navigate to the DM thread URL using the recipient's user ID.
        # Instagram DM URLs use the thread_id, but we can also open a DM
        # with a user via https://www.instagram.com/direct/t/{thread_id}/
        # Since we have recipient_id (user pk), we first need the thread_id.
        # We can find it from the inbox, or navigate directly to the user's DM.
        # The simplest approach: navigate to the inbox and find the thread.
        await self.navigate("https://www.instagram.com/direct/inbox/")
        await asyncio.sleep(3)

        # 2. Find the thread with this recipient and click it
        click_result = await self.evaluate(f"""async () => {{
            try {{
                // Fetch inbox to find the thread_id for this recipient
                const r = await fetch(
                    "https://www.instagram.com/api/v1/direct_v2/inbox/?limit=50",
                    {{
                        headers: {{"x-ig-app-id": "936619743392459"}},
                        credentials: "include"
                    }}
                );
                if (!r.ok) return {{error: "Inbox fetch failed: HTTP " + r.status}};
                const data = await r.json();
                const threads = data.inbox?.threads || [];
                const targetId = "{recipient_id}";
                const thread = threads.find(t =>
                    (t.users || []).some(u => String(u.pk) === targetId)
                );
                if (!thread) return {{error: "Thread not found for recipient " + targetId}};
                const threadId = thread.thread_id;

                // Navigate to the thread
                window.location.href = "https://www.instagram.com/direct/t/" + threadId + "/";
                return {{status: "navigating", thread_id: threadId}};
            }} catch(e) {{
                return {{error: e.message}};
            }}
        }}""")

        result = click_result.get("result", click_result) if isinstance(click_result, dict) else click_result
        if isinstance(result, dict) and result.get("error"):
            return result

        await asyncio.sleep(4)

        # 3. Type the message in the contenteditable editor
        type_result = await self.evaluate(f"""() => {{
            // Instagram DM input is a contenteditable div
            const editor = document.querySelector(
                'div[contenteditable="true"][role="textbox"], ' +
                'div[contenteditable="true"][data-lexical-editor="true"], ' +
                'textarea[placeholder*="Message"], ' +
                'div[contenteditable="true"]'
            );
            if (!editor) return {{error: 'Could not find message input'}};

            editor.focus();
            if (editor.isContentEditable) {{
                document.execCommand('insertText', false, {escaped_text});
            }} else {{
                editor.value = {escaped_text};
                editor.dispatchEvent(new Event('input', {{bubbles: true}}));
            }}
            return {{status: 'typed'}};
        }}""")

        result = type_result.get("result", type_result) if isinstance(type_result, dict) else type_result
        if isinstance(result, dict) and result.get("error"):
            return result

        await asyncio.sleep(1)

        # 4. Click Send button or press Enter
        send_result = await self.evaluate("""() => {
            // Instagram DM send button
            const sendBtn = document.querySelector(
                'button[type="button"][aria-label*="Send"], ' +
                'button[type="submit"], ' +
                'div[role="button"][aria-label*="Send"]'
            );
            if (sendBtn) {
                sendBtn.click();
                return {status: 'sent', method: 'button'};
            }
            // Try pressing Enter on the editor
            const editor = document.querySelector(
                'div[contenteditable="true"], textarea'
            );
            if (editor) {
                editor.dispatchEvent(
                    new KeyboardEvent('keydown', {
                        key: 'Enter', keyCode: 13, which: 13, bubbles: true
                    })
                );
                return {status: 'sent', method: 'enter'};
            }
            return {status: 'no_button'};
        }""")

        await asyncio.sleep(2)

        send_result = send_result.get("result", send_result) if isinstance(send_result, dict) else send_result
        if isinstance(send_result, dict) and send_result.get("status") == "no_button":
            return {"error": "Could not find send button or press Enter"}

        return {"status": "ok", "sent": True, "recipient_id": recipient_id, "text": text}
