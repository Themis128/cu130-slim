#!/usr/bin/env bash
# Update LinkedIn personal About section via browser sidecar
# Usage: li-personal-update-about.sh "About text" OR li-personal-update-about.sh /path/to/file.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

ABOUT_ARG="${1:?Usage: li-personal-update-about.sh <about>}"
SIDECAR="http://localhost:9225"

if [ -f "$ABOUT_ARG" ]; then
  ABOUT=$(cat "$ABOUT_ARG")
else
  ABOUT="$ABOUT_ARG"
fi

echo "→ Updating LinkedIn personal About (${#ABOUT} chars)..."

echo "$ABOUT" > /tmp/li-personal-about.txt

python3 << 'PYEOF'
import requests, json, time

with open('/tmp/li-personal-about.txt') as f:
    about = f.read()

SIDECAR = 'http://localhost:9225'

# Navigate to profile
requests.post(f'{SIDECAR}/debug/navigate', json={'url': 'https://www.linkedin.com/in/baltzakis-themis/'}, timeout=30)
time.sleep(5)

# Click "Edit about" button
resp = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const btns = document.querySelectorAll('button, a');
    for (const b of btns) {
        const aria = b.getAttribute('aria-label') || '';
        if (aria === 'Edit about') { b.click(); await new Promise(r => setTimeout(r, 3000)); return 'clicked'; }
    }
    return 'not found';
})()
'''}, timeout=15)
print(f'  Edit button: {resp.json().get("result","")}')

# Set the about text
resp2 = requests.post(f'{SIDECAR}/debug/eval', json={'script': f'''
(function(){{
    const t = document.querySelector('textarea');
    if(!t) return 'no textarea';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(t, {json.dumps(about)});
    t.dispatchEvent(new Event('input', {{bubbles: true}}));
    return 'set: ' + t.value.length + ' chars';
}})()
'''}, timeout=10)
print(f'  Set: {resp2.json().get("result","")}')

time.sleep(1)

# Save
resp3 = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
        if (b.textContent.trim() === 'Save') { b.click(); await new Promise(r => setTimeout(r, 5000)); return 'saved'; }
    }
    return 'no save button';
})()
'''}, timeout=30)
print(f'  Save: {resp3.json().get("result","")}')

import os
os.remove('/tmp/li-personal-about.txt')
PYEOF
