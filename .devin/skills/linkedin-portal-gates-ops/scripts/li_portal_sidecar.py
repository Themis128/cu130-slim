#!/usr/bin/env python3
"""li_portal_sidecar.py — probe/drive LinkedIn developer-portal gates
THROUGH the linkedin-browser-sidecar (:9225) session, no CDP needed.

The sidecar holds the real logged-in LinkedIn session; the :9229 debug
Chrome portal session dies often (cookie expiry + Cloudflare challenge).
This tool reuses the sidecar's /debug/navigate + /debug/eval surface —
same DOM probes as member_dp_probe.py but over HTTP.

    li_portal_sidecar.py              # read-only state report
    li_portal_sidecar.py --json       # machine-readable
    li_portal_sidecar.py --drive      # best-effort Member DP request click
    li_portal_sidecar.py --url URL    # probe a different portal page

Respects the sidecar circuit breaker: refuses to navigate while
rate_limited/circuit-open (check /health first; --force clears via
POST /session/clear-rate-limit then retries once).
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("LI_SIDECAR", "http://linkedin-browser-sidecar:9225")
APP_URL = "https://www.linkedin.com/developers/apps/265077368"
APPS_URL = "https://www.linkedin.com/developers/apps"

STATE_JS = r"""
const text = document.body ? document.body.innerText : '';
const loggedIn = !!document.querySelector('img.global-nav__me-photo, '
  + 'button.global-nav__primary-link-me, [data-control-name="nav.settings"]');
const rows = [];
document.querySelectorAll('a,button,[role=button]').forEach(el => {
  const t = (el.textContent || '').trim();
  if (/request access|member dp|matched audience|community management|lead sync|pages dp|advertising api|events api/i.test(t) && t.length < 120)
    rows.push({text: t, tag: el.tagName, href: el.href || null});
});
return {url: location.href, loggedIn, title: document.title,
        rows: rows.slice(0, 40), snippet: text.slice(0, 600)};
"""


def _req(method: str, path: str, body: dict | None = None,
         timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return {"error": f"HTTP {e.code}: " + e.read()[:300].decode()}
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)[:200]}


def health() -> dict:
    return _req("GET", "/health", timeout=15)


def navigate(url: str, force: bool = False) -> dict:
    h = health()
    if h.get("rate_limited") and not force:
        return {"error": "sidecar rate-limited/circuit-open — "
                         "retry after cooldown or pass --force"}
    if force:
        _req("POST", "/session/clear-rate-limit", {}, timeout=15)
    return _req("POST", "/debug/navigate", {"url": url})


def eval_js(expr: str) -> dict:
    return _req("POST", "/debug/eval", {"expr": expr}, timeout=45)


def probe(url: str) -> dict:
    nav = navigate(url)
    if "error" in nav:
        return {"stage": "navigate", **nav}
    time.sleep(6)
    r = eval_js(STATE_JS)
    val = r.get("value")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            pass
    return {"stage": "eval", "nav": nav.get("url"), "state": val,
            "error": r.get("error")}


def drive_member_dp() -> dict:
    """Click the Member DP (Member) 'Request access' and dump the modal."""
    click = eval_js(r"""
const els = [...document.querySelectorAll('a,button,[role=button],li,div')];
const target = els.find(el => {
  const t = (el.textContent || '').trim();
  return /member dp/i.test(t) && t.length < 200;
});
if (!target) return {clicked: false, why: 'no Member DP row'};
const btn = [...target.querySelectorAll('a,button,[role=button]')]
  .find(b => /request access/i.test(b.textContent || ''));
if (!btn) return {clicked: false, why: 'row found, no request button'};
btn.click();
return {clicked: true};
""")
    time.sleep(3)
    modal = eval_js(r"""
return {modalText: (document.querySelector('[role=dialog],.artdeco-modal,'
  + '.jobs-easy-apply-modal,.modal__overlay') || document.body)
  .innerText.slice(0, 1200),
  url: location.href};
""")
    return {"click": click.get("value"), "modal": modal.get("value")}


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    js = "--json" in args
    drive = "--drive" in args
    force = "--force" in args
    url = APP_URL
    if "--url" in args:
        url = args[args.index("--url") + 1]

    out = probe(url)
    if drive and not out.get("error"):
        out["drive"] = drive_member_dp()
    print(json.dumps(out, indent=2) if js else
          json.dumps(out, indent=2)[:4000])
    return 0 if not out.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
