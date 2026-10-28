#!/usr/bin/env python3
"""Update LinkedIn personal contact info (website) via browser sidecar.
Usage: li-personal-update-website.py "https://cloudless.gr"
Note: LinkedIn contact info uses dynamic input IDs. This script tries the
built-in endpoint first, then falls back to manual browser automation."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _li_org_update import evaluate, post  # noqa: E402
from _common import http  # noqa: E402
from skill_http import usage  # noqa: E402

SIDECAR = "http://localhost:9225"

def re_search_error(text: str) -> bool:
    return any(s in text for s in ("error", "Error", "FAILED")) or not text


url = sys.argv[1] if len(sys.argv) > 1 else usage('li-personal-update-website.py "https://cloudless.gr"')
print(f"→ Updating LinkedIn personal website to {url}...")

result = http("POST", f"{SIDECAR}/profile/website", json_body={"website": url})

if not re_search_error(result):
    print(f"  {result}")
    sys.exit(0)

print("  Built-in endpoint failed, using manual browser automation...")
post("/debug/navigate", {"url": "https://www.linkedin.com/in/baltzakis-themis/"})
time.sleep(5)

click_js = lambda text: f"""(async () => {{
    const els = document.querySelectorAll('a, button, span, div');
    for (const e of els) {{
        if (e.textContent.trim() === '{text}') {{ e.click(); await new Promise(r => setTimeout(r, 3000)); return 'clicked'; }}
    }}
    return 'not found';
}})()"""

print(f'  Contact info: {evaluate(click_js("Contact info"), 15)}')
print(f'  Edit: {evaluate(click_js("Edit contact info"), 15)}')

domain = url.replace("https://", "").replace("http://", "")
existing = evaluate(f"""(function() {{
    const inputs = document.querySelectorAll('input[type=text]');
    for (const i of inputs) {{
        if (i.value && i.value.includes('{domain}')) {{
            return 'already exists: ' + i.value;
        }}
    }}
    return 'not found';
}})()""", 10)

if "already exists" in existing:
    print(f"  Website already exists: {existing}")
    evaluate("""(async () => {
        const btns = document.querySelectorAll('button');
        for (const b of btns) {
            if (b.textContent.trim() === 'Cancel' || b.getAttribute('aria-label') === 'Close') { b.click(); await new Promise(r => setTimeout(r, 2000)); return 'closed'; }
        }
        return 'no close';
    })()""", 10)
    sys.exit(0)

print(f'  Add website: {evaluate(click_js("Add website"), 15)}')

set_js = f"""(function() {{
    const inputs = document.querySelectorAll('input[type=text]');
    for (const i of inputs) {{
        if (!i.value && i.id && !i.id.includes('search') && !i.id.includes('phone')) {{
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(i, {json.dumps(url)});
            i.dispatchEvent(new Event('input', {{bubbles: true}}));
            i.dispatchEvent(new Event('change', {{bubbles: true}}));
            i.dispatchEvent(new Event('blur', {{bubbles: true}}));
            return 'set: ' + i.value;
        }}
    }}
    return 'no empty input found';
}})()"""
print(f"  Set: {evaluate(set_js, 10)}")
time.sleep(2)

print(f'  Save: {evaluate("""(async () => {
    const btns = document.querySelectorAll(\'button\');
    for (const b of btns) {
        if (b.textContent.trim() === \'Save\') { b.click(); await new Promise(r => setTimeout(r, 5000)); return \'saved\'; }
    }
    return \'no save button\';
})()""", 30)}')
