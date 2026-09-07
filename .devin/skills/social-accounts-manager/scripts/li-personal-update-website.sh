#!/usr/bin/env bash
# Update LinkedIn personal contact info (website) via browser sidecar
# Usage: li-personal-update-website.sh "https://cloudless.gr"
# Note: LinkedIn contact info uses dynamic input IDs. This script navigates
# to the profile, opens Contact info, clicks Edit, and adds/updates the website.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

URL="${1:?Usage: li-personal-update-website.sh <url>}"
SIDECAR="http://localhost:9225"

echo "→ Updating LinkedIn personal website to $URL..."

# Try the sidecar's built-in website endpoint first
RESULT=$(curl -sf -X POST "$SIDECAR/profile/website" \
  -H "Content-Type: application/json" \
  -d "{\"website\":\"$URL\"}" 2>&1 || echo "FAILED")

if echo "$RESULT" | grep -q "error\|Error\|FAILED"; then
  echo "  Built-in endpoint failed, using manual browser automation..."
  
  # Navigate to profile
  curl -sf -X POST "$SIDECAR/debug/navigate" \
    -H "Content-Type: application/json" \
    -d '{"url":"https://www.linkedin.com/in/baltzakis-themis/"}' > /dev/null
  sleep 5

  python3 << PYEOF
import requests, json, time

url = "$URL"
SIDECAR = 'http://localhost:9225'

# Click "Contact info"
resp = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const els = document.querySelectorAll('a, button, span, div');
    for (const e of els) {
        if (e.textContent.trim() === 'Contact info') { e.click(); await new Promise(r => setTimeout(r, 3000)); return 'clicked'; }
    }
    return 'not found';
})()
'''}, timeout=15)
print(f'  Contact info: {resp.json().get("result","")}')

# Click "Edit contact info"
resp2 = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const els = document.querySelectorAll('a, button');
    for (const e of els) {
        if (e.textContent.trim() === 'Edit contact info') { e.click(); await new Promise(r => setTimeout(r, 3000)); return 'clicked'; }
    }
    return 'not found';
})()
'''}, timeout=15)
print(f'  Edit: {resp2.json().get("result","")}')

# Check if URL is already in one of the website inputs
resp3 = requests.post(f'{SIDECAR}/debug/eval', json={'script': f'''
(function() {{
    const inputs = document.querySelectorAll('input[type=text]');
    for (const i of inputs) {{
        if (i.value && i.value.includes('{url.replace("https://","").replace("http://","")}')) {{
            return 'already exists: ' + i.value;
        }}
    }}
    return 'not found';
}})()
'''}, timeout=10)
existing = resp3.json().get('result','')
if 'already exists' in existing:
    print(f'  Website already exists: {existing}')
    # Close dialog
    requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
        (async () => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                if (b.textContent.trim() === 'Cancel' || b.getAttribute('aria-label') === 'Close') { b.click(); await new Promise(r => setTimeout(r, 2000)); return 'closed'; }
            }
            return 'no close';
        })()
    '''}, timeout=10)
    exit(0)

# Click "Add website" to add a new website entry
resp4 = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const els = document.querySelectorAll('a, button, div');
    for (const e of els) {
        if (e.textContent.trim() === 'Add website') { e.click(); await new Promise(r => setTimeout(r, 3000)); return 'clicked'; }
    }
    return 'not found';
})()
'''}, timeout=15)
print(f'  Add website: {resp4.json().get("result","")}')

# Find the new empty website input and set it
resp5 = requests.post(f'{SIDECAR}/debug/eval', json={'script': f'''
(function() {{
    const inputs = document.querySelectorAll('input[type=text]');
    for (const i of inputs) {{
        // Find an empty input that's in the website section
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
}})()
'''}, timeout=10)
print(f'  Set: {resp5.json().get("result","")}')

time.sleep(2)

# Save
resp6 = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
        if (b.textContent.trim() === 'Save') { b.click(); await new Promise(r => setTimeout(r, 5000)); return 'saved'; }
    }
    return 'no save button';
})()
'''}, timeout=30)
print(f'  Save: {resp6.json().get("result","")}')
PYEOF
else
  echo "  $RESULT"
fi
