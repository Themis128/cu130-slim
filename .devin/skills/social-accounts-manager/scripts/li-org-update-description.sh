#!/usr/bin/env bash
# Update LinkedIn Organization description (2000 char limit) via browser sidecar
# Usage: li-org-update-description.sh "Description text" OR li-org-update-description.sh /path/to/file.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

DESC_ARG="${1:?Usage: li-org-update-description.sh <description>}"
SIDECAR="http://localhost:9225"
ORG_ID="108614163"

# If argument is a file path, read from file
if [ -f "$DESC_ARG" ]; then
  DESC=$(cat "$DESC_ARG")
else
  DESC="$DESC_ARG"
fi

echo "→ Updating LinkedIn org description (${#DESC} chars)..."

# Navigate to edit page
curl -sf -X POST "$SIDECAR/debug/navigate" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.linkedin.com/company/'$ORG_ID'/admin/edit/?editPageActiveTab=details"}' > /dev/null
sleep 5

# Write description to a temp file for Python to read
echo "$DESC" > /tmp/li-org-desc.txt

python3 << 'PYEOF'
import requests, json, time

with open('/tmp/li-org-desc.txt') as f:
    desc = f.read()

SIDECAR = 'http://localhost:9225'

# Set description
resp = requests.post(f'{SIDECAR}/debug/eval', json={'script': f'''
(function(){{
    const t = document.querySelector('textarea#organization-description-field');
    if(!t) return 'not found';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(t, {json.dumps(desc)});
    t.dispatchEvent(new Event('input', {{bubbles: true}}));
    t.dispatchEvent(new Event('change', {{bubbles: true}}));
    t.dispatchEvent(new Event('blur', {{bubbles: true}}));
    return 'set: ' + t.value.length + ' chars';
}})()
'''}, timeout=10)
print(f'  Set: {resp.json().get("result","")}')

time.sleep(2)

# Save
resp2 = requests.post(f'{SIDECAR}/debug/eval', json={'script': '''
(async () => {
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
        if (b.textContent.trim() === 'Save') { b.click(); await new Promise(r => setTimeout(r, 8000)); return 'saved'; }
    }
    return 'no save button';
})()
'''}, timeout=30)
print(f'  Save: {resp2.json().get("result","")}')

import os
os.remove('/tmp/li-org-desc.txt')
PYEOF
