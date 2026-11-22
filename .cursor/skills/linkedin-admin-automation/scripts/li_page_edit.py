#!/usr/bin/env python3
"""LinkedIn Company Page field editor via linkedin-browser-sidecar.

Drives the admin edit UI (/debug/*) — the only working write path while
Organizations API edits are gated behind Marketing Developer Platform
access. Uses real keystrokes (Ember dirty-flag safe).

Usage:
  li_page_edit.py get tagline|description
  li_page_edit.py set tagline "New tagline text"
  li_page_edit.py set description "Multi\nline\ndescription"
  li_page_edit.py set tagline --file /path/to/text.txt

Fields map to the admin edit form:
  tagline     -> #organization-tagline-field   (Page info tab, default)
  description -> #organization-description-field (Details tab — auto-clicked)

Env: SIDECAR_URL (default http://127.0.0.1:9225), ORG_ID (default
108614163 = cloudless.gr). Requires sidecar has_session:true.
"""

import argparse
import json
import os
import sys
import time
import urllib.request

SIDECAR = os.environ.get("SIDECAR_URL", "http://127.0.0.1:9225").rstrip("/")
ORG_ID = os.environ.get("ORG_ID", "108614163")
EDIT_URL = f"https://www.linkedin.com/company/{ORG_ID}/admin/edit/"

FIELDS = {
    "tagline": {
        "id": "organization-tagline-field",
        "tab": "Page info",
    },
    "description": {
        "id": "organization-description-field",
        "tab": "Details",
    },
}


def call(method: str, path: str, payload: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        SIDECAR + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def eval_js(script: str):
    res = call("POST", "/debug/eval", {"script": script}, timeout=15)
    return res.get("result", res)


def field_value(field_id: str) -> str | None:
    v = eval_js(
        f"const t=document.getElementById('{field_id}'); t?t.value:null"
    )
    return v if isinstance(v, str) else None


def ensure_visible(spec: dict) -> bool:
    """Activate the field's tab if its panel is hidden, then confirm."""
    fid = spec["id"]
    if eval_js(f"!!document.getElementById('{fid}')?.offsetParent"):
        return True
    eval_js(
        "const tab=[...document.querySelectorAll('[role=tab],a,button')]"
        f".find(e=>(e.innerText||'').trim()==='{spec['tab']}');"
        "if(tab){tab.click();'clicked'}else{'no tab'}"
    )
    time.sleep(2)
    return bool(eval_js(f"!!document.getElementById('{fid}')?.offsetParent"))


def open_editor() -> bool:
    call("POST", "/debug/navigate", {"url": EDIT_URL}, timeout=40)
    time.sleep(6)
    return True


def get_field(name: str) -> int:
    spec = FIELDS[name]
    open_editor()
    if not ensure_visible(spec):
        print(f"FAIL: {name} field not reachable", file=sys.stderr)
        return 1
    print(field_value(spec["id"]) or "")
    return 0


def set_field(name: str, text: str) -> int:
    spec = FIELDS[name]
    fid = spec["id"]

    open_editor()
    if not ensure_visible(spec):
        print(f"FAIL: {name} field not reachable", file=sys.stderr)
        return 1

    # Real click to focus, select-all via selection range, then real
    # keystrokes WITHOUT a selector — /debug/type with a selector re-clicks
    # the element and collapses the selection (learned 2026-11-23).
    call("POST", "/debug/click", {"selector": f"#{fid}"}, timeout=15)
    time.sleep(0.5)
    eval_js(
        f"const t=document.getElementById('{fid}');"
        "t.focus();t.setSelectionRange(0,t.value.length);'ok'"
    )
    time.sleep(0.3)
    call("POST", "/debug/type", {"text": text, "delay": 10}, timeout=180)

    if field_value(fid) != text:
        print("FAIL: field value mismatch after typing", file=sys.stderr)
        return 1

    eval_js(
        "const b=[...document.querySelectorAll('button')]"
        ".find(e=>e.offsetParent&&(e.innerText||'').trim()==='Save');"
        "if(b){b.click();'saved'}else{'no save btn'}"
    )
    time.sleep(3)
    eval_js(  # dismiss "Share your page edits" modal if it appears
        "const b=[...document.querySelectorAll('button,a')]"
        ".find(e=>e.offsetParent&&(e.innerText||'').trim()==='No thanks');"
        "if(b){b.click();'dismissed'}else{'no modal'}"
    )
    time.sleep(1)

    # Verify persistence — fresh load re-reads from the server.
    open_editor()
    ensure_visible(spec)
    final = field_value(fid)
    if final != text:
        print(f"FAIL: post-save verify mismatch: {final!r}", file=sys.stderr)
        return 1
    print(f"OK: {name} updated and verified ({len(text)} chars)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get")
    g.add_argument("field", choices=sorted(FIELDS))
    s = sub.add_parser("set")
    s.add_argument("field", choices=sorted(FIELDS))
    s.add_argument("text", nargs="?", default=None)
    s.add_argument("--file", dest="file", default=None)
    args = ap.parse_args()

    try:
        health = call("GET", "/health", timeout=10)
    except Exception as e:
        print(f"FAIL: sidecar unreachable: {e}", file=sys.stderr)
        return 1
    if not health.get("has_session"):
        print("FAIL: sidecar has no LinkedIn session", file=sys.stderr)
        return 1

    if args.cmd == "get":
        return get_field(args.field)

    if args.file:
        text = open(args.file, encoding="utf-8").read()
    elif args.text is not None:
        text = args.text.replace("\\n", "\n")
    else:
        print("FAIL: set requires text arg or --file", file=sys.stderr)
        return 2
    if not text.strip():
        print("FAIL: empty text", file=sys.stderr)
        return 2
    return set_field(args.field, text)


if __name__ == "__main__":
    sys.exit(main())
