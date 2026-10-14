"""LinkedIn newsletter issue publishing via the shared browser-novnc bridge.

Fallback browser path for POST /api/v1/newsletter/linkedin/issue when the
dedicated linkedin-browser-sidecar fails (editor splash loop, renderer
OOM, anti-bot fingerprint). The bridge's persistent headed Chromium
profile holds a long-lived LinkedIn session that hydrates /article/new/
reliably.

Mirrors the operator runbook verified live for The Cloudless Build Log
Issue #2 (2026-10-14): .devin/skills/linkedin-newsletter-ops/.

Editor quirks handled here (see the skill for the full matrix):
- Trusted Types blocks insertHTML — body goes in through a synthetic
  ClipboardEvent with a DataTransfer payload.
- The payload is base64 in transit; the browser decodes it with
  TextDecoder('utf-8'). atob() alone returns Latin-1 bytes and mangles
  every non-ASCII char (— → â€").
- The companion publish post is a Quill editor, not ProseMirror — plain
  insertText, not HTML paste.
- select-all + paste can append a second copy — content is cleared via a
  DOM range delete first.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError

logger = logging.getLogger(__name__)

EDITOR_URL = "https://www.linkedin.com/article/new/"
FEED_URL = "https://www.linkedin.com/feed/"

# A lone "â"/"Ã"/"ð" prefixing punctuation is the atob-Latin-1 signature.
_MOJIBAKE_RE = re.compile(r"[âÃð][€€™œœ\x80-\xbf]")


class LinkedInNewsletterBridgeError(Exception):
    """Raised when the bridge publish path fails mid-flow."""


def _js_str(value: str) -> str:
    """Escape a Python string for embedding in a JS double-quoted literal."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )


class LinkedInNewsletterBridge:
    """Drive the bridge's logged-in LinkedIn session through the article editor."""

    def __init__(self, client: BrowserBridgeClient | None = None) -> None:
        self._client = client or BrowserBridgeClient(
            get_settings().BROWSER_BRIDGE_URL,
            timeout=90.0,
            platform="linkedin",
        )

    async def _eval(self, expression: str) -> Any:
        data = await self._client.evaluate(expression)
        result = data.get("result")
        if result is None:
            raise LinkedInNewsletterBridgeError(f"evaluate returned no result: {data}")
        return result

    async def _wait_editor(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                state = json.loads(
                    await self._eval(
                        'JSON.stringify({u:location.href.slice(0,70),'
                        'pm:!!document.querySelector("div.ProseMirror[contenteditable=true]")})'
                    )
                )
                if state.get("pm"):
                    return True
            except (BrowserBridgeError, LinkedInNewsletterBridgeError, json.JSONDecodeError):
                pass  # page context destroyed mid-navigation — keep polling
            await asyncio.sleep(4)
        return False

    async def _paste_body(self, html_body: str) -> int:
        # Clear first (DOM range delete — execCommand selectAll does not reach
        # the PM model), then paste in a separate tick: ProseMirror applies the
        # synthetic paste asynchronously, and a same-tick clear+paste can race
        # the MutationObserver so the DOM reverts to empty on the next sync.
        await self._eval(
            '(()=>{const pm=document.querySelector("div.ProseMirror[contenteditable=true]");'
            'if(!pm)return "NO-PM";pm.focus();'
            'const sel=document.getSelection();const r=document.createRange();'
            'r.selectNodeContents(pm);sel.removeAllRanges();sel.addRange(r);'
            'document.execCommand("delete");return "cleared"})()'
        )
        await asyncio.sleep(0.5)
        b64 = base64.b64encode(html_body.encode("utf-8")).decode()
        paste_expr = (
            '(()=>{const pm=document.querySelector("div.ProseMirror[contenteditable=true]");'
            'if(!pm)return JSON.stringify({err:"NO-PM"});pm.focus();'
            f'const bytes=Uint8Array.from(atob("{b64}"),c=>c.charCodeAt(0));'
            'const html=new TextDecoder("utf-8").decode(bytes);'
            'const dt=new DataTransfer();'
            'dt.setData("text/html",html);'
            'pm.dispatchEvent(new ClipboardEvent("paste",'
            '{clipboardData:dt,bubbles:true,cancelable:true}));'
            'return "ok"})()'
        )
        verify_expr = (
            '(()=>{const pm=document.querySelector("div.ProseMirror[contenteditable=true]");'
            'if(!pm)return JSON.stringify({err:"NO-PM"});'
            'const t=(pm.innerText||"").trim();'
            'return JSON.stringify({words:t.split(/\\s+/).filter(Boolean).length,'
            'sample:t.slice(0,400)});})()'
        )
        for attempt in range(2):
            await self._eval(paste_expr)
            # Verify AFTER a settle delay — the sync-time DOM can show the
            # words while PM's model later reverts them ("Body text is
            # required" at publish). Only the persisted state counts.
            await asyncio.sleep(1.5)
            result = json.loads(await self._eval(verify_expr))
            if result.get("words"):
                break
        if result.get("err") or not result.get("words"):
            raise LinkedInNewsletterBridgeError(f"body paste failed: {result}")
        if _MOJIBAKE_RE.search(result.get("sample") or ""):
            raise LinkedInNewsletterBridgeError(
                "pasted body shows Latin-1 mojibake — refusing to publish"
            )
        return int(result["words"])

    async def _set_seo(self, seo_title: str, seo_description: str) -> None:
        # The Manage dropdown lazy-renders on a trusted click — a Playwright
        # force-click does NOT open it. A JS .click() on the toggle does.
        # Items are <li> holding div[role=button] inside a <ul> with no
        # role=menu, so match by exact text within the manage-menu subtree.
        await self._eval(
            '(()=>{const btn=document.querySelector(".article-editor-manage-menu button");'
            'if(!btn)return "no-manage";btn.click();return "clicked"})()'
        )
        clicked = "notfound"
        for _ in range(6):
            await asyncio.sleep(1.5)
            clicked = await self._eval(
                '(()=>{const li=[...document.querySelectorAll('
                '".article-editor-manage-menu li")]'
                '.find(e=>(e.innerText||"").trim()==="Settings");'
                'if(!li)return "notfound";'
                '(li.querySelector("[role=button]")||li).click();'
                'return "clicked"})()'
            )
            if clicked == "clicked":
                break
        if clicked != "clicked":
            raise LinkedInNewsletterBridgeError("Manage → Settings menu item not found")
        await asyncio.sleep(3)
        ids = json.loads(
            await self._eval(
                '(()=>{const dlg=document.querySelector("[role=dialog],.artdeco-modal");'
                'if(!dlg)return "[]";'
                'const ins=[...dlg.querySelectorAll("input,textarea")].map(e=>e.id);'
                'return JSON.stringify(ins)})()'
            )
        )
        if len(ids) < 2:
            raise LinkedInNewsletterBridgeError(f"SEO dialog fields not found: {ids}")
        # SEO title max is 60 chars — a longer value makes Save silently
        # refuse (dialog stays open, no error). Truncate rather than stall.
        for field_id, value in ((ids[0], seo_title[:60]), (ids[1], seo_description)):
            if not value:
                continue
            await self._eval(
                f'(()=>{{const el=document.getElementById("{field_id}");'
                'const proto=el.tagName==="TEXTAREA"?HTMLTextAreaElement:HTMLInputElement;'
                'const s=Object.getOwnPropertyDescriptor(proto.prototype,"value").set;'
                f's.call(el,"{_js_str(value)}");'
                'el.dispatchEvent(new Event("input",{bubbles:true}));'
                'return el.value.length})()'
            )
        # JS click inside the modal + verify it closes — a Playwright
        # force-click on "Save" can land on a stale match and leave the
        # dialog open, which then swallows the publish-dialog detection.
        await self._eval(
            '(()=>{const dlg=[...document.querySelectorAll("[role=dialog],'
            '.artdeco-modal")].filter(d=>d.offsetParent).pop();'
            'const btn=[...dlg.querySelectorAll("button")]'
            '.find(b=>(b.innerText||"").trim()==="Save");'
            'if(!btn)return "no-save";btn.click();return "clicked"})()'
        )
        for _ in range(6):
            await asyncio.sleep(1.5)
            open_dlgs = await self._eval(
                'JSON.stringify([...document.querySelectorAll("[role=dialog],'
                '.artdeco-modal")].filter(d=>d.offsetParent).length)'
            )
            if not int(open_dlgs):
                return
        raise LinkedInNewsletterBridgeError(
            "SEO dialog did not close after Save — check field validation"
        )

    async def _set_schedule(self, schedule_iso: str) -> None:
        """Drive the publish dialog's "Schedule post" picker.

        Opens via ``button[aria-label="Schedule post"]``. The picker's
        ``#share-post__scheduled-date``/``…-time`` text inputs are display-
        only — setting them programmatically does NOT move the model (the
        header keeps showing the default). Real selection means clicking a
        calendar day cell (``button[data-calendar-day]`` with a full
        ``aria-label`` like "Wednesday, October 21, 2026.") and a
        ``[role=option]`` in the expanded timepicker listbox — both with a
        full pointer event sequence (JS .click() alone does not commit).
        "Next" returns to the publish dialog where the primary button
        becomes "Schedule".
        """
        try:
            dt = datetime.fromisoformat(schedule_iso)
        except ValueError as exc:
            raise LinkedInNewsletterBridgeError(
                f"invalid schedule_iso: {schedule_iso}"
            ) from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("Europe/Athens"))
        local = dt.astimezone(ZoneInfo("Europe/Athens"))
        # e.g. "Wednesday, October 21, 2026" — calendar aria-label prefix
        day_label = local.strftime("%A, %B ") + str(local.day) + local.strftime(", %Y")
        hour12 = local.hour % 12 or 12
        time_label = f"{hour12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"

        clicked = await self._eval(
            '(()=>{const dlg=[...document.querySelectorAll("[role=dialog]")]'
            '.filter(d=>d.offsetParent).pop();if(!dlg)return "no-dialog";'
            'const btn=[...dlg.querySelectorAll("button")]'
            '.find(b=>(b.getAttribute("aria-label")||"")==="Schedule post");'
            'if(!btn)return "no-schedule";btn.click();return "clicked"})()'
        )
        if clicked != "clicked":
            raise LinkedInNewsletterBridgeError(
                f"Schedule post button: {clicked}"
            )
        for _ in range(8):
            await asyncio.sleep(1)
            ready = await self._eval(
                '!!document.getElementById("share-post__scheduled-date")'
            )
            if ready:
                break
        else:
            raise LinkedInNewsletterBridgeError("schedule picker did not open")

        # Calendar day — full pointer sequence (a lone .click() is ignored
        # and Playwright flags the cell "not visible").
        day_hit = await self._eval(
            '(()=>{const dlg=[...document.querySelectorAll("[role=dialog],'
            '.artdeco-modal")].filter(d=>d.offsetParent).pop();'
            f'const lbl="{_js_str(day_label)}";'
            'const b=[...dlg.querySelectorAll("button[data-calendar-day]")]'
            '.find(b=>(b.getAttribute("aria-label")||"").startsWith(lbl));'
            'if(!b)return "no-day";'
            'const rc=b.getBoundingClientRect();'
            'const o={bubbles:true,cancelable:true,composed:true,view:window,'
            'clientX:rc.x+rc.width/2,clientY:rc.y+rc.height/2,button:0,'
            'pointerId:1,isPrimary:true,pointerType:"mouse"};'
            'b.dispatchEvent(new PointerEvent("pointerdown",o));'
            'b.dispatchEvent(new MouseEvent("mousedown",o));'
            'b.dispatchEvent(new PointerEvent("pointerup",o));'
            'b.dispatchEvent(new MouseEvent("mouseup",o));'
            'b.dispatchEvent(new MouseEvent("click",o));'
            'return "ok"})()'
        )
        if day_hit != "ok":
            raise LinkedInNewsletterBridgeError(
                f"calendar day not found for {day_label}: {day_hit}"
            )
        await asyncio.sleep(1)
        got = await self._eval(
            'document.getElementById("share-post__scheduled-date").value'
        )
        expected_date = local.strftime("%m/%d/%Y")
        if got != expected_date:
            raise LinkedInNewsletterBridgeError(
                f"calendar pick failed: expected {expected_date}, got {got}"
            )

        # Timepicker — expand, scroll target option into view, pointer-click.
        await self._eval(
            '(()=>{const dlg=[...document.querySelectorAll("[role=dialog],'
            '.artdeco-modal")].filter(d=>d.offsetParent).pop();'
            'const b=[...dlg.querySelectorAll("button")]'
            '.find(b=>/Expand timepicker/i.test((b.innerText||"")'
            '+(b.getAttribute("aria-label")||"")));'
            'if(b)b.click();return !!b})()'
        )
        await asyncio.sleep(1.5)
        time_hit = await self._eval(
            '(()=>{const opts=[...document.querySelectorAll("[role=option],'
            '[role=listbox] li,[role=listbox] div")].filter(e=>e.offsetParent);'
            f'const t=opts.find(e=>(e.innerText||"").trim()==="{time_label}");'
            'if(!t)return "no-option";'
            't.scrollIntoView({block:"center"});'
            'const rc=t.getBoundingClientRect();'
            'const o={bubbles:true,cancelable:true,composed:true,view:window,'
            'clientX:rc.x+rc.width/2,clientY:rc.y+rc.height/2,button:0,'
            'pointerId:1,isPrimary:true,pointerType:"mouse"};'
            't.dispatchEvent(new PointerEvent("pointerdown",o));'
            't.dispatchEvent(new MouseEvent("mousedown",o));'
            't.dispatchEvent(new PointerEvent("pointerup",o));'
            't.dispatchEvent(new MouseEvent("mouseup",o));'
            't.dispatchEvent(new MouseEvent("click",o));'
            'return "ok"})()'
        )
        if time_hit != "ok":
            raise LinkedInNewsletterBridgeError(
                f"time option {time_label} not found: {time_hit}"
            )
        await asyncio.sleep(1)
        got_time = await self._eval(
            'document.getElementById("share-post__scheduled-time").value'
        )
        if got_time != time_label:
            raise LinkedInNewsletterBridgeError(
                f"timepick failed: expected {time_label}, got {got_time}"
            )

        # "Next" commits the picked date/time back to the publish dialog.
        clicked = await self._eval(
            '(()=>{const dlg=[...document.querySelectorAll("[role=dialog],'
            '.artdeco-modal")].filter(d=>d.offsetParent).pop();'
            'const btn=[...dlg.querySelectorAll("button")]'
            '.find(b=>(b.innerText||"").trim()==="Next");'
            'if(!btn)return "no-next";btn.click();return "clicked"})()'
        )
        if clicked != "clicked":
            raise LinkedInNewsletterBridgeError(f"schedule Next: {clicked}")
        await asyncio.sleep(3)
        confirm = await self._eval(
            '(()=>{const dlg=[...document.querySelectorAll("[role=dialog]")]'
            '.filter(d=>d.offsetParent).pop();'
            'return dlg?(dlg.innerText||"").slice(0,300):"none"})()'
        )
        if "Posting" not in str(confirm):
            raise LinkedInNewsletterBridgeError(
                f"schedule not committed: {confirm}"
            )

    async def publish_issue(
        self,
        *,
        title: str,
        html_body: str,
        cover_base64: str | None = None,
        seo_title: str | None = None,
        seo_description: str | None = None,
        post_description: str | None = None,
        schedule_iso: str | None = None,
        dry_run: bool = False,
        editor_timeout_s: float = 180.0,
    ) -> dict[str, Any]:
        """Drive the bridge through editor open → publish. Returns steps + URL."""
        steps: list[str] = []

        # interactive=True → 900s busy-hold so hourly pollers cannot preempt
        # mid-publish; force=True recovers wedged sessions (manual recovery).
        await self._client.start_session(
            "linkedin", force=True, interactive=True, contention_retries=6
        )
        steps.append("bridge session claimed")
        await asyncio.sleep(6)

        # Warm the feed first — it boots the SPA/service-worker and caches the
        # editor bundles; a cold /article/new/ can splash-loop for minutes.
        await self._client.navigate(FEED_URL)
        await asyncio.sleep(6)
        steps.append("feed warmed")

        mounted = False
        for attempt in range(2):
            await self._client.navigate(EDITOR_URL)
            timeout = editor_timeout_s if attempt == 0 else 90.0
            if await self._wait_editor(timeout):
                mounted = True
                break
            steps.append(f"editor not mounted after attempt {attempt + 1} — retrying warm")
        if not mounted:
            raise LinkedInNewsletterBridgeError(
                "article editor did not mount after warm retry"
            )
        steps.append("editor mounted")

        await self._client.fill(".article-editor-headline__textarea", title)
        words = await self._paste_body(html_body)
        steps.append(f"title+body filled ({words} words)")

        if cover_base64:
            uploaded = await self._client.upload_file(
                "input[type=file]",
                file_base64=cover_base64,
                file_name="newsletter-cover.png",
                click_selector='button:has-text("Upload from computer")',
            )
            if not uploaded.get("uploaded"):
                raise LinkedInNewsletterBridgeError(f"cover upload failed: {uploaded}")
            await asyncio.sleep(5)
            # The upload leaves the media-editor modal open — its own "Next"
            # confirms the cover; leaving it up swallows the Manage click.
            await self._eval(
                '(()=>{const m=document.querySelector('
                '".article-editor__media-editor-modal");if(!m)return "absent";'
                'const btn=[...m.querySelectorAll("button")]'
                '.find(b=>(b.innerText||"").trim()==="Next");'
                'if(!btn)return "no-next";btn.click();return "closed"})()'
            )
            await asyncio.sleep(2)
            steps.append("cover uploaded")

        if seo_title or seo_description:
            await self._set_seo(seo_title or "", seo_description or "")
            steps.append("seo set")

        # Click the LAST visible Next — the media modal can leave a stale
        # Next button in the DOM, and the first match may be inside a hidden
        # layer. Then poll for the dialog — render time varies.
        edit_url = await self._eval("location.href")
        for nav_attempt in range(2):
            await self._eval(
                '(()=>{const btns=[...document.querySelectorAll("button")]'
                '.filter(b=>b.offsetParent&&(b.innerText||"").trim().startsWith("Next"));'
                'if(!btns.length)return "no-next";btns[btns.length-1].click();'
                'return "clicked"})()'
            )
            dialogs: list[str] = []
            for _ in range(10):
                await asyncio.sleep(1.5)
                dialogs = json.loads(
                    await self._eval(
                        'JSON.stringify([...document.querySelectorAll("[role=dialog]")]'
                        '.map(d=>d.innerText.slice(0,500)))'
                    )
                )
                if any(
                    "edition of your newsletter" in d
                    or "Post to Anyone + Subscribers" in d
                    for d in dialogs
                ):
                    break
            if any(
                "edition of your newsletter" in d
                or "Post to Anyone + Subscribers" in d
                for d in dialogs
            ):
                break
            # Ember's click listener can wedge after earlier modal churn —
            # the draft is auto-saved, so reload once and retry Next.
            if nav_attempt == 0 and isinstance(edit_url, str) and "/article/edit/" in edit_url:
                steps.append("no publish dialog — reloading editor")
                await self._client.navigate(edit_url)
                if not await self._wait_editor(90):
                    raise LinkedInNewsletterBridgeError(
                        "editor did not remount after reload"
                    )
                continue
            raise LinkedInNewsletterBridgeError(f"publish dialog not found: {dialogs}")
        steps.append("publish dialog open")

        if post_description:
            # Quill announcement editor — plain text only, newlines to spaces.
            announcement = _js_str(post_description.replace("\n", " "))
            await self._eval(
                '(()=>{const ed=document.querySelector("[role=dialog] '
                'div[contenteditable=true]");if(!ed)return 0;ed.focus();'
                f'document.execCommand("insertText",false,"{announcement}");'
                "return ed.innerText.length})()"
            )
            await asyncio.sleep(1)
            steps.append("announcement text set")

        if schedule_iso:
            await self._set_schedule(schedule_iso)
            steps.append("scheduled")

        if dry_run:
            steps.append("dry-run: publish dialog open, not publishing")
            return {
                "published": False,
                "article_url": None,
                "steps": steps,
                "via": "browser-bridge",
            }

        # After _set_schedule the primary button reads "Schedule" instead of
        # "Publish" — accept whichever is present.
        wanted = "Schedule" if schedule_iso else "Publish"
        clicked = await self._eval(
            '(()=>{const dlg=[...document.querySelectorAll("[role=dialog]")]'
            '.filter(d=>d.offsetParent).pop();if(!dlg)return "no-dialog";'
            'const btn=[...dlg.querySelectorAll("button")]'
            f'.find(b=>(b.innerText||"").trim()==="{wanted}");'
            'if(!btn)return "no-"+("'
            + wanted.lower()
            + '");btn.click();return "clicked"})()'
        )
        if clicked != "clicked":
            raise LinkedInNewsletterBridgeError(f"{wanted} button: {clicked}")
        await asyncio.sleep(6)
        url = await self._eval("location.href")
        steps.append("published" if not schedule_iso else "schedule confirmed")
        return {
            "published": not schedule_iso,
            "scheduled": bool(schedule_iso),
            "article_url": url,
            "steps": steps,
            "via": "browser-bridge",
        }
