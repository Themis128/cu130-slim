#!/usr/bin/env python3
"""Revoke stale Polar org access tokens by name pattern — CDP-driven.

Uses the logged-in Chrome session (:9222) on a dedicated tab that is always
closed afterwards (no popup residue). Clicks are JS `el.click()` — plain
locator clicks time out because Revoke sits inside per-row dropdown menus.

Usage:
    python polar_token_cleanup.py --keep devin-mcp3 socialauto-production \
        --match 'devin-mcp'
    # dry-run (default off): --dry-run prints rows without clicking
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys

PAGE_URL = "https://polar.sh/dashboard/cloudless/settings/access-tokens"


async def revoke_stale(keep: list[str], match: str, dry_run: bool) -> int:
    from playwright.async_api import async_playwright

    keep_re = "|".join(re.escape(k) for k in keep) if keep else r"$^"
    async with async_playwright() as pw:
        b = await pw.chromium.connect_over_cdp("http://localhost:9222")
        ctx = b.contexts[0]
        page = await ctx.new_page()
        try:
            await page.goto(PAGE_URL, timeout=25000)
            await page.wait_for_timeout(4500)

            for _ in range(10):
                # find each stale row's Revoke control + its token name via JS
                rows = await page.evaluate("""([keepRe]) => {
                    const out = [];
                    const kR = new RegExp(keepRe);
                    const seen = new Set();
                    document.querySelectorAll('*').forEach(e => {
                        if (e.childElementCount !== 0) return;
                        if ((e.textContent||'').trim() !== 'Revoke') return;
                        let n = e, name = null;
                        for (let i = 0; i < 12 && n; i++) {
                            const m = (n.textContent || '').match(/(devin-mcp\\d*|socialauto-production)/);
                            if (m) { name = m[1]; break; }   // first name match = row level
                            n = n.parentElement;
                        }
                        if (name && !seen.has(e)) { seen.add(e); out.push({name, stale: !kR.test(name)}); }
                    });
                    return out;
                }""", [keep_re])
                stale = [r["name"] for r in rows if r["stale"]]
                if not stale:
                    break
                if dry_run:
                    print("dry-run stale rows:", stale)
                    return 0
                # JS-click the first stale Revoke (opens confirm), then confirm
                clicked = await page.evaluate("""([staleNames]) => {
                    for (const e of document.querySelectorAll('*')) {
                        if (e.childElementCount !== 0) continue;
                        if ((e.textContent||'').trim() !== 'Revoke') continue;
                        let n = e, name = null;
                        for (let i = 0; i < 12 && n; i++) {
                            const m = (n.textContent||'').match(/(devin-mcp\\d*|socialauto-production)/);
                            if (m) { name = m[1]; break; }   // first name match = row level
                            n = n.parentElement;
                        }
                        // STRICT equality against the stale set — an ancestor
                        // regex match can hit a sibling row inside the table.
                        if (name && staleNames.includes(name)) { e.click(); return name; }
                    }
                    return null;
                }""", [stale])
                if not clicked:
                    break
                print("revoke clicked:", clicked)
                await page.wait_for_timeout(1500)
                # confirm dialog demands TYPING the token name — fill via the
                # native setter (React controlled), then click enabled Revoke.
                await page.evaluate("""(name) => {
                    const dlg = document.querySelector('[role=dialog]');
                    if (!dlg) return false;
                    const m = dlg.innerText.match(/enter "([^"]+)"/);
                    if (!m || m[1] !== name) return false;      // never type the wrong name
                    const inp = dlg.querySelector('input');
                    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                    setter.call(inp, name);
                    inp.dispatchEvent(new Event('input', {bubbles: true}));
                    inp.dispatchEvent(new Event('change', {bubbles: true}));
                    return true;
                }""", clicked)
                await page.wait_for_timeout(700)
                await page.evaluate("""() => {
                    const dlg = document.querySelector('[role=dialog]');
                    if (!dlg) return false;
                    for (const b of dlg.querySelectorAll('button')) {
                        if (/revoke/i.test(b.textContent) && !b.disabled) { b.click(); return true; }
                    }
                    return false;
                }""")
                await page.wait_for_timeout(2000)

            body = await page.inner_text("body")
            remaining = sorted(set(re.findall(r"devin-mcp\d*|socialauto-production", body)))
            print("remaining tokens:", remaining)
            return 0
        finally:
            await page.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", nargs="*", default=["devin-mcp3", "socialauto-production"])
    ap.add_argument("--match", default="devin-mcp")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return asyncio.run(revoke_stale(args.keep, args.match, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
