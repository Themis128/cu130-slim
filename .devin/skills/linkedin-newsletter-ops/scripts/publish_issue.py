#!/usr/bin/env python3
"""Publish a LinkedIn newsletter issue via the browser-novnc bridge (:9223).

Verified path used for The Cloudless Build Log Issue #2 (2026-10-14) — the
bridge's persistent Chromium profile holds a live LinkedIn session that
hydrates /article/new/ reliably where the dedicated sidecar gets splash-
looped by anti-bot.

Usage:
  publish_issue.py --title "..." --body-html /path/article.html \
      --cover /path/cover.png [--seo-title "..."] [--seo-desc "..."] \
      [--post-text "..."] [--dry-run] [--container browser-novnc]

Requires: bridge session for platform=linkedin already logged in
(profile persists across restarts; if not, run once with --interactive and
log in via noVNC at http://localhost:6080/vnc.html).
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import api_base, request  # noqa: E402

BRIDGE = api_base("BROWSER_BRIDGE_URL", "http://localhost:9223")
EDITOR_URL = "https://www.linkedin.com/article/new/"
H = {"X-Platform": "linkedin"}


def call(method: str, path: str, data: dict | None = None, timeout: int = 60):
    return request(method, f"{BRIDGE}{path}", data=data, headers=H, timeout=timeout)


def evaluate(expr: str, timeout: int = 30):
    d = call("POST", "/session/evaluate", {"expression": expr}, timeout)
    res = d.get("result")
    if res is None:
        raise RuntimeError(f"evaluate failed: {d}")
    return res


def wait_editor(timeout_s: int = 120) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            state = json.loads(evaluate(
                'JSON.stringify({u:location.href.slice(0,70),'
                'pm:!!document.querySelector("div.ProseMirror[contenteditable=true]")})'
            ))
            if state.get("pm"):
                return True
        except Exception:
            pass  # context destroyed mid-navigation — keep polling
        time.sleep(4)
    return False


def fill_title(title: str):
    d = call("POST", "/session/fill",
             {"selector": ".article-editor-headline__textarea", "value": title})
    if not d.get("filled"):
        raise RuntimeError(f"title fill failed: {d}")


def paste_body(html: str) -> int:
    # Clear first (DOM range delete — execCommand selectAll does not extend
    # into the PM model), then paste in a SEPARATE call: PM applies the
    # synthetic paste asynchronously and a same-tick clear+paste can race the
    # MutationObserver so the model later reverts the DOM to empty
    # ("Body text is required" at publish despite paste having "worked").
    evaluate(
        '(()=>{const pm=document.querySelector("div.ProseMirror[contenteditable=true]");'
        'if(!pm)return "NO-PM";pm.focus();'
        'const sel=document.getSelection();const r=document.createRange();'
        'r.selectNodeContents(pm);sel.removeAllRanges();sel.addRange(r);'
        'document.execCommand("delete");return "cleared"})()')
    time.sleep(1)
    b64 = base64.b64encode(html.encode()).decode()
    paste_expr = (
        '(()=>{const pm=document.querySelector("div.ProseMirror[contenteditable=true]");'
        'if(!pm)return "NO-PM";pm.focus();'
        # atob() yields raw bytes — feeding them straight to setData reads them
        # as Latin-1 and mangles every non-ASCII char (— → â€", € → â‚¬).
        # Decode as UTF-8 via TextDecoder first.
        f'const bytes=Uint8Array.from(atob("{b64}"),c=>c.charCodeAt(0));'
        'const html=new TextDecoder("utf-8").decode(bytes);'
        'const dt=new DataTransfer();'
        'dt.setData("text/html",html);'
        'pm.dispatchEvent(new ClipboardEvent("paste",'
        '{clipboardData:dt,bubbles:true,cancelable:true}));'
        'return "ok"})()')
    verify_expr = (
        '(()=>{const pm=document.querySelector("div.ProseMirror[contenteditable=true]");'
        'return JSON.stringify({words:(pm.innerText||"").trim()'
        '.split(/\\s+/).filter(Boolean).length})})()')
    for _ in range(2):
        evaluate(paste_expr, timeout=60)
        time.sleep(2)  # settle — only the persisted word count is real
        res = json.loads(evaluate(verify_expr))
        if res.get("words"):
            return int(res["words"])
    raise RuntimeError("body paste did not persist after retry")


def upload_cover(png_path: str):
    # file_base64 avoids the docker cp dance entirely — the bridge decodes
    # to a private temp file inside its own container.
    b64 = base64.b64encode(Path(png_path).read_bytes()).decode()
    d = call("POST", "/session/upload", {
        "selector": "input[type=file]",
        "file_base64": b64,
        "file_name": "newsletter-cover.png",
        "click_selector": 'button:has-text("Upload from computer")',
    }, timeout=120)
    if not d.get("uploaded"):
        raise RuntimeError(f"cover upload failed: {d}")
    time.sleep(5)
    # The upload leaves the media-editor modal open — its own Next confirms
    # the cover; leaving it up swallows the Manage click.
    evaluate('(()=>{const m=document.querySelector('
             '".article-editor__media-editor-modal");if(!m)return "absent";'
             'const btn=[...m.querySelectorAll("button")]'
             '.find(b=>(b.innerText||"").trim()==="Next");'
             'if(!btn)return "no-next";btn.click();return "closed"})()')
    time.sleep(2)


def set_seo(seo_title: str, seo_desc: str):
    # The Manage dropdown lazy-renders on a trusted click — a Playwright
    # force-click does NOT open it; a JS .click() on the toggle does. Menu
    # items are <li> holding div[role=button] in a <ul> with no role=menu.
    evaluate('(()=>{const btn=document.querySelector('
             '".article-editor-manage-menu button");'
             'if(!btn)return "no-manage";btn.click();return "clicked"})()')
    for _ in range(6):
        time.sleep(2)
        clicked = evaluate(
            '(()=>{const li=[...document.querySelectorAll('
            '".article-editor-manage-menu li")]'
            '.find(e=>(e.innerText||"").trim()==="Settings");'
            'if(!li)return "notfound";'
            '(li.querySelector("[role=button]")||li).click();'
            'return "clicked"})()')
        if clicked == "clicked":
            break
    else:
        raise RuntimeError("Manage → Settings menu item not found")
    time.sleep(3)
    ids = json.loads(evaluate(
        '(()=>{const dlg=document.querySelector("[role=dialog],.artdeco-modal");'
        'const ins=[...dlg.querySelectorAll("input,textarea")].map(e=>e.id);'
        'return JSON.stringify(ins)})()'))
    if len(ids) < 2:
        raise RuntimeError(f"SEO dialog fields not found: {ids}")
    for field_id, value in ((ids[0], seo_title), (ids[1], seo_desc)):
        if not value:
            continue
        v = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        evaluate(
            f'(()=>{{const el=document.getElementById("{field_id}");'
            'const proto=el.tagName==="TEXTAREA"?HTMLTextAreaElement:HTMLInputElement;'
            'const s=Object.getOwnPropertyDescriptor(proto.prototype,"value").set;'
            f's.call(el,"{v}");el.dispatchEvent(new Event("input",{{bubbles:true}}));'
            'return el.value.length})()')
    call("POST", "/session/click", {"selector": "button", "text": "Save"})
    time.sleep(2)


def publish(post_text: str, dry_run: bool) -> str:
    # Click the LAST visible Next — the media modal can leave a stale Next in
    # the DOM; the first match may sit inside a hidden layer.
    evaluate('(()=>{const btns=[...document.querySelectorAll("button")]'
             '.filter(b=>b.offsetParent&&(b.innerText||"").trim().startsWith("Next"));'
             'if(!btns.length)return "no-next";btns[btns.length-1].click();'
             'return "clicked"})()')
    dlg: list[str] = []
    for _ in range(10):
        time.sleep(2)
        dlg = json.loads(evaluate(
            'JSON.stringify([...document.querySelectorAll("[role=dialog]")]'
            '.map(d=>d.innerText.slice(0,500)))'))
        if any("edition of your newsletter" in t or "Post to Anyone + Subscribers" in t
               for t in dlg):
            break
    else:
        raise RuntimeError(f"publish dialog not found: {dlg}")
    if post_text:
        t = post_text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        evaluate('(()=>{const ed=document.querySelector("[role=dialog] '
                 'div[contenteditable=true]");ed.focus();'
                 f'document.execCommand("insertText",false,"{t}");'
                 "return ed.innerText.length})()")
        time.sleep(1)
    if dry_run:
        return "DRY-RUN: dialog open, not publishing"
    clicked = evaluate(
        '(()=>{const dlg=[...document.querySelectorAll("[role=dialog]")]'
        '.filter(d=>d.offsetParent).pop();if(!dlg)return "no-dialog";'
        'const btn=[...dlg.querySelectorAll("button")]'
        '.find(b=>(b.innerText||"").trim()==="Publish");'
        'if(!btn)return "no-publish";btn.click();return "clicked"})()')
    if clicked != "clicked":
        raise RuntimeError(f"Publish button: {clicked}")
    time.sleep(6)
    return evaluate("location.href", timeout=15)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--body-html", required=True, type=Path)
    ap.add_argument("--cover", required=True, type=Path)
    ap.add_argument("--seo-title", default="")
    ap.add_argument("--seo-desc", default="")
    ap.add_argument("--post-text", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--editor-timeout", type=int, default=180)
    args = ap.parse_args()

    html = args.body_html.read_text()
    print("[1/6] claiming bridge session for linkedin…", flush=True)
    call("POST", "/session/start",
         {"platform": "linkedin", "interactive": True, "force": True}, timeout=60)
    time.sleep(6)

    print("[2/6] navigating to article editor…", flush=True)
    call("POST", "/session/navigate", {"url": EDITOR_URL}, timeout=90)
    if not wait_editor(args.editor_timeout):
        raise SystemExit("editor did not mount — see probe_editor.py / SKILL.md")
    print("      editor mounted", flush=True)

    print("[3/6] filling title + body…", flush=True)
    fill_title(args.title)
    words = paste_body(html)
    print(f"      body pasted ({words} words)", flush=True)

    print("[4/6] uploading cover…", flush=True)
    upload_cover(str(args.cover))

    if args.seo_title or args.seo_desc:
        print("[5/6] SEO settings…", flush=True)
        set_seo(args.seo_title, args.seo_desc)
    else:
        print("[5/6] SEO skipped", flush=True)

    print("[6/6] publish dialog…", flush=True)
    result = publish(args.post_text, args.dry_run)
    print("RESULT:", result)


if __name__ == "__main__":
    main()
