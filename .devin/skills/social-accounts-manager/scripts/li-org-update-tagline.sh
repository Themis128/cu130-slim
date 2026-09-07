#!/usr/bin/env bash
# Update LinkedIn Organization tagline via browser sidecar
# Usage: li-org-update-tagline.sh "Tagline text"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

TAGLINE="${1:?Usage: li-org-update-tagline.sh <tagline>}"
SIDECAR="http://localhost:9225"
ORG_ID="108614163"

echo "→ Updating LinkedIn org tagline (${#TAGLINE} chars)..."

# Navigate to edit page
curl -sf -X POST "$SIDECAR/debug/navigate" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.linkedin.com/company/'$ORG_ID'/admin/edit/?editPageActiveTab=details"}' > /dev/null
sleep 5

# Update tagline and save
python3 << PYEOF
import requests, json, time

tagline = $(python3 -c "import json; print(json.dumps('$TAGLINE'))")

# Set tagline
resp = requests.post('$SIDECAR/debug/eval', json={'script': f'''
(function(){{
    const t = document.querySelector('textarea#organization-tagline-field');
    if(!t) return 'not found';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(t, {json.dumps(tagline)});
    t.dispatchEvent(new Event('input', {{bubbles: true}}));
    t.dispatchEvent(new Event('change', {{bubbles: true}}));
    t.dispatchEvent(new Event('blur', {{bubbles: true}}));
    return 'set: ' + t.value.length + ' chars';
}})()
'''}, timeout=10)
print(f'  Set: {resp.json().get("result","")}')

time.sleep(2)

# Save
resp2 = requests.post('$SIDECAR/debug/eval', json={'script': '''
(async () => {
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
        if (b.textContent.trim() === 'Save') { b.click(); await new Promise(r => setTimeout(r, 8000)); return 'saved'; }
    }
    return 'no save button';
})()
'''}, timeout=30)
print(f'  Save: {resp2.json().get("result","")}')
PYEOF
