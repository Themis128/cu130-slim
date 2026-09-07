#!/usr/bin/env bash
# Update LinkedIn personal contact info (website) via browser sidecar
# Usage: li-personal-update-website.sh "https://cloudless.gr"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

URL="${1:?Usage: li-personal-update-website.sh <url>}"
SIDECAR="http://localhost:9225"

echo "→ Updating LinkedIn personal website to $URL..."

# Try the sidecar's built-in website endpoint first
RESULT=$(curl -sf -X POST "$SIDECAR/profile/website" \
  -H "Content-Type: application/json" \
  -d "{\"website\":\"$URL\"}" 2>&1)

if echo "$RESULT" | grep -q "error\|Error"; then
  echo "  Built-in endpoint failed, trying manual browser automation..."
  
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

# Find website input and update it
resp3 = requests.post(f'{SIDECAR}/debug/eval', json={'script': f'''
(async () => {{
    const inputs = document.querySelectorAll('input');
    for (const i of inputs) {{
        if (i.placeholder && i.placeholder.includes('website') || i.placeholder.includes('URL')) {{
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(i, {json.dumps(url)});
            i.dispatchEvent(new Event('input', {{bubbles: true}}));
            return 'set: ' + i.value;
        }}
    }}
    return 'no website input found';
}})()
'''}, timeout=10)
print(f'  Set: {resp3.json().get("result","")}')

time.sleep(1)

# Save
resp4 = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
        if (b.textContent.trim() === 'Save' || b.textContent.trim() === 'Apply') { b.click(); await new Promise(r => setTimeout(r, 5000)); return 'saved'; }
    }
    return 'no save button';
})()
'''}, timeout=30)
print(f'  Save: {resp4.json().get("result","")}')
PYEOF
else
  echo "  $RESULT"
fi
