#!/usr/bin/env python3
"""Transplant the live Facebook session from the FB sidecar (:9226) into the
Playwright MCP persistent profile, then verify login on facebook.com.

The MCP browser cannot read httpOnly cookies, but document.cookie CAN write
their values — so we navigate to facebook.com first, then inject.

Usage: python3 transplant_fb.py
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                     / "../playwright-mcp-driver/scripts"))
from mcp_client import PlaywrightMCP  # noqa: E402

SIDECAR = "http://localhost:9226"
KEEP = {"c_user", "xs", "datr", "fr", "sb", "wd", "dpr", "presence",
        "locale", "usida", "oo"}


def export_sidecar_cookies() -> dict:
    with urllib.request.urlopen(f"{SIDECAR}/debug/all-cookies",
                                timeout=20) as r:
        data = json.load(r)
    cookies = data.get("cookies", data)
    return {k: v for k, v in cookies.items() if k in KEEP}


def main() -> int:
    cookies = export_sidecar_cookies()
    if "c_user" not in cookies or "xs" not in cookies:
        print("ERROR: sidecar export missing c_user/xs — sidecar session dead")
        return 1
    with PlaywrightMCP() as mcp:
        mcp.tool("browser_navigate", url="https://www.facebook.com/")
        js = ("() => { const c = %s; for (const [k,v] of Object.entries(c)) "
              "document.cookie = `${k}=${v}; path=/; domain=.facebook.com; secure`; "
              "return document.cookie.length; }") % json.dumps(cookies)
        mcp.tool("browser_evaluate", function=js)
        mcp.tool("browser_navigate", url="https://www.facebook.com/")
        time.sleep(3)
        out = mcp.tool("browser_evaluate", function="""() => ({
          loginForm: !!document.querySelector('input[name=email]'),
          commentBox: !!document.querySelector('div[contenteditable][aria-label*="Comment as"]'),
          whoami: (document.querySelector('div[contenteditable][aria-label*="Comment as"]')||{getAttribute:()=>null}).getAttribute('aria-label')
        })""")
        print(out.split("### Result")[-1].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
