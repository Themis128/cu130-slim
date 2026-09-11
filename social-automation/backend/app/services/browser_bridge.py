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

        # Session is active and logged in
        if status.get("status") == "active" and status.get("cookies_found"):
            return {"status": "active", "message": "Session active"}

        # Session is waiting, error, or has no cookies — restart it
        try:
            await self.start_session(platform)
        except BrowserBridgeError:
            pass

        return {
            "status": "waiting",
            "message": f"Browser session not logged in. Open noVNC and log in to {platform}.",
            "novnc_url": "http://localhost:6080/vnc.html",
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
        Requires a logged-in Facebook browser session.
        """
        await self.navigate("https://www.facebook.com/messages/")
        await asyncio.sleep(4)

        result = await self.evaluate("""() => {
            const conversations = [];
            // Facebook Messenger conversation list items
            const items = document.querySelectorAll(
                '[role="navigation"] a[href*="/messages/"], ' +
                'a[href*="/messages/t/"], ' +
                '[role="listitem"] a[href*="/messages/"]'
            );

            items.forEach(item => {
                const href = item.getAttribute('href') || '';
                const text = item.innerText || '';
                const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                if (lines.length === 0) return;

                // Extract thread ID from URL
                const match = href.match(/messages\\/t\\/([0-9]+)/);
                const threadId = match ? match[1] : null;

                conversations.push({
                    name: lines[0] || 'Unknown',
                    preview: lines[1] || '',
                    thread_id: threadId,
                    url: href,
                    unread: text.includes('•') || item.querySelector('[aria-label*="unread"]') !== null,
                });
            });

            return { conversations, count: conversations.length };
        }""")
        raw = result.get("result", {})
        return {
            "conversations": raw.get("conversations", []),
            "count": raw.get("count", 0),
        }

    async def get_personal_messenger_messages(self, thread_id: str) -> dict[str, Any]:
        """Read messages from a specific conversation thread.

        Navigates to the thread URL and extracts all visible messages.
        """
        await self.navigate(f"https://www.facebook.com/messages/t/{thread_id}/")
        await asyncio.sleep(4)

        result = await self.evaluate("""() => {
            const messages = [];
            // Facebook message containers
            const msgElements = document.querySelectorAll(
                '[role="main"] [data-scope="messages_table"] > div, ' +
                '[role="main"] div[role="row"], ' +
                'div[aria-label*="Message"] > div'
            );

            msgElements.forEach(el => {
                const text = el.innerText || '';
                if (!text.trim()) return;

                // Try to determine sender — outgoing messages have different styling
                const isOutgoing = el.closest('[style*="flex-end"]') !== null ||
                                   el.parentElement?.style?.alignSelf === 'flex-end' ||
                                   text.includes('You:');

                const timeEl = el.querySelector('time, [data-absolute-time]');
                const timestamp = timeEl ? timeEl.getAttribute('datetime') ||
                                          timeEl.getAttribute('data-absolute-time') ||
                                          timeEl.innerText : null;

                messages.push({
                    text: text.replace(/^You:\\s*/, '').trim(),
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

    async def send_personal_messenger_message(self, thread_id: str, text: str) -> dict[str, Any]:
        """Send a message in a personal Facebook Messenger conversation.

        Navigates to the thread, types in the message input, and presses Enter.
        """
        await self.navigate(f"https://www.facebook.com/messages/t/{thread_id}/")
        await asyncio.sleep(4)

        # Find the message input box and type
        await self.evaluate(f"""(function() {{
            // Facebook Messenger message input — contenteditable div
            const input = document.querySelector(
                '[contenteditable="true"][role="textbox"], ' +
                'div[role="textbox"][contenteditable], ' +
                '[data-contents="true"][contenteditable]'
            );
            if (!input) return {{ found: false }};

            input.focus();
            // Use execCommand for contenteditable
            document.execCommand('insertText', false, {json.dumps(text)});
            return {{ found: true }};
        }})()""")

        await asyncio.sleep(1)

        # Press Enter to send
        result = await self.evaluate("""(function() {
            const input = document.querySelector(
                '[contenteditable="true"][role="textbox"], ' +
                'div[role="textbox"][contenteditable], ' +
                '[data-contents="true"][contenteditable]'
            );
            if (!input) return { found: false };

            input.focus();
            // Simulate Enter keypress
            const event = new KeyboardEvent('keydown', {
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13,
                bubbles: true,
                cancelable: true,
            });
            input.dispatchEvent(event);

            // Also try pressing the send button as fallback
            const sendBtn = document.querySelector(
                '[aria-label="Send"], [aria-label="Press Enter to send"]'
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
