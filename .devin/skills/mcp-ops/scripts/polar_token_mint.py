#!/usr/bin/env python3
"""Mint a Polar access token by driving the live Chrome CDP session (:9222).

Flow:
  1. Open https://polar.sh/dashboard — if the page shows a sign-in wall,
     try "Sign in with Google" once (works when the profile's Google session
     is warm — same lane as the CF gates). If still walled, exit 2 with a
     clear one-time-sign-in message.
  2. Dashboard → Settings → Developers → Access tokens → Create token.
  3. Fill a name ("devin-mcp"), pick scopes (default: all read+write the
     dashboard offers on the simple create form), submit.
  4. Scrape the revealed token, validate it via api.polar.sh, write
     POLAR_ACCESS_TOKEN to the repo .env (via polar_token_setup logic),
     smoke-test the polar-mcp wrapper.

Usage: python polar_token_mint.py [--name devin-mcp]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GR_PATH = REPO / ".devin/skills/operator-gate-ops/scripts/gate_runner.py"

spec = importlib.util.spec_from_file_location("gr", GR_PATH)
gr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gr)


def eval_js(p, expr: str, timeout: int = 10):
    return p.poll(expr, timeout=timeout)


def click_text(p, needle: str, timeout: int = 5) -> bool:
    """Click the first visible element whose text contains `needle`."""
    js = f"""(() => {{
        const els = [...document.querySelectorAll('button,a,[role=button],[role=link]')];
        const el = els.find(e => e.offsetParent && e.innerText.trim().toLowerCase().includes({json.dumps(needle.lower())}));
        if (!el) return false;
        el.scrollIntoView({{block:'center'}}); el.click(); return true;
    }})()"""
    try:
        return bool(eval_js(p, js, timeout))
    except Exception:
        return False


def body_text(p, timeout: int = 15) -> str:
    return eval_js(p, "document.body.innerText", timeout) or ""


def ensure_signed_in(p) -> bool:
    p.goto("https://polar.sh/dashboard")
    time.sleep(5)
    t = body_text(p)
    if "Sign in" not in t or ("Settings" in t or "Overview" in t or "Products" in t):
        return True
    # Try the Google SSO lane once
    if click_text(p, "Sign in with Google"):
        time.sleep(8)
        # Google account chooser — click the listed account if present
        click_text(p, "@") or click_text(p, "Continue as")
        time.sleep(6)
        t = body_text(p)
        if "Sign in" not in t or "Overview" in t or "Products" in t:
            return True
    return False


def create_token(p, name: str) -> str | None:
    for path in ("settings/developers", "settings/developer-tokens", "developers"):
        p.goto(f"https://polar.sh/dashboard/{path}")
        time.sleep(4)
        t = body_text(p).lower()
        if "access token" in t or "api" in t:
            break
    t = body_text(p)
    if not click_text(p, "Create") and not click_text(p, "New token") and not click_text(p, "Create token"):
        print("[polar] no create control found — page state:\n", t[:300])
        return None
    time.sleep(3)
    # Fill the name field — first visible text input
    eval_js(p, """(() => {
        const inp = [...document.querySelectorAll('input[type=text],input:not([type])')].find(i => i.offsetParent);
        if (!inp) return false;
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        setter.call(inp, %s);
        inp.dispatchEvent(new Event('input', {bubbles:true}));
        return true;
    })()""" % json.dumps(name), timeout=5)
    time.sleep(1)
    click_text(p, "Create token") or click_text(p, "Create") or click_text(p, "Generate")
    time.sleep(4)
    t = body_text(p)
    # Polar tokens look like polar_oat_*
    m = re.search(r"(polar_[a-z]+_[A-Za-z0-9_-]{16,})", t)
    if m:
        return m.group(1)
    # Fallback: scrape any revealed/copyable value field
    val = eval_js(p, """(() => {
        const el = [...document.querySelectorAll('code,pre,input')].find(e => /polar_[a-z]+_/.test(e.value || e.textContent || ''));
        return el ? (el.value || el.textContent) : null;
    })()""", timeout=5)
    return val.strip() if isinstance(val, str) else None


def write_and_verify(token: str, name: str) -> int:
    r = subprocess.run([sys.executable, str(REPO / ".devin/skills/mcp-ops/scripts/polar_token_setup.py"), token],
                       capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip()[:200])
    return 0 if "written" in r.stdout else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="devin-mcp")
    args = ap.parse_args()

    gr._ensure_cdp()
    p = gr.Page()
    if not ensure_signed_in(p):
        print("[polar] sign-in wall persists — open https://polar.sh/dashboard in the debug Chrome")
        print("        (remote-debugging :9222), sign in once, then re-run this script.")
        return 2
    print("[polar] signed in — creating token")
    token = create_token(p, args.name)
    if not token:
        print("[polar] token capture failed — inspect the page; DOM may differ from the simple create form")
        return 1
    print(f"[polar] token captured ({token[:14]}…)")
    return write_and_verify(token, args.name)


if __name__ == "__main__":
    sys.exit(main())
