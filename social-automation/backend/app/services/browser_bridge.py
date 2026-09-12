"""HTTP client for the browser-novnc bridge container.

The browser-novnc container runs a headed Chromium with noVNC for visual
login and a FastAPI bridge on port 9223.  This client wraps the bridge
endpoints so the SocialAuto backend can use the logged-in browser session
as a fallback when the aiograpi-rest sidecar fails (challenge_required,
fingerprint mismatch, etc.).

Currently supports Instagram profile read/write via the browser's
authenticated fetch to Instagram's internal web API.
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

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/health")
            resp.raise_for_status()
            return resp.json()

    async def start_session(self, platform: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/start",
                json={"platform": platform},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def session_status(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
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

        # Session is active and logged in — bridge returns "active" or "done"
        # (after cookie extraction) with cookies_found populated
        if status.get("status") in ("active", "done") and status.get("cookies_found"):
            return {"status": "active", "message": "Session active"}

        # Try extracting cookies — the browser may be logged in but the
        # session status hasn't been updated yet
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}/session/extract")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("cookies_found"):
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/login",
                json={"username": username, "password": password},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def navigate(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/navigate",
                json={"url": url},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def extract_cookies(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/session/extract")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def stop_session(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/session/stop")
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    # ── Instagram profile via browser ────────────────────────────────────

    async def get_instagram_profile(self) -> dict[str, Any]:
        """Read the Instagram profile from the logged-in browser session."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/click",
                json={"selector": selector, "text": text},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def fill(self, selector: str, value: str) -> dict[str, Any]:
        """Fill an input/textarea via the bridge."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/fill",
                json={"selector": selector, "value": value},
            )
            if resp.status_code >= 400:
                raise BrowserBridgeError(resp.status_code, resp.text)
            return resp.json()

    async def evaluate(self, expression: str) -> dict[str, Any]:
        """Evaluate JS in the browser and return the result."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/session/evaluate",
                json={"expression": expression},
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
                await self.click('div[role="dialog"] button[type="submit"], div[role="dialog"] button:has-text("Continue"), div[role="dialog"] button:has-text("Submit")')
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
