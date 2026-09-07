#!/usr/bin/env bash
# Update LinkedIn Organization website via browser sidecar
# Usage: li-org-update-website.sh "https://cloudless.gr"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

URL="${1:?Usage: li-org-update-website.sh <url>}"
SIDECAR="http://localhost:9225"
ORG_ID="108614163"

echo "→ Updating LinkedIn org website to $URL..."

curl -sf -X POST "$SIDECAR/debug/navigate" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.linkedin.com/company/'$ORG_ID'/admin/edit/?editPageActiveTab=details"}' > /dev/null
sleep 5

python3 << PYEOF
import requests, json, time

url = "$URL"
SIDECAR = 'http://localhost:9225'

resp = requests.post(f'{SIDECAR}/debug/eval', json={'script': f'''
(function(){{
    const t = document.querySelector('input#organization-website-field');
    if(!t) return 'not found';
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(t, {json.dumps(url)});
    t.dispatchEvent(new Event('input', {{bubbles: true}}));
    t.dispatchEvent(new Event('change', {{bubbles: true}}));
    t.dispatchEvent(new Event('blur', {{bubbles: true}}));
    return 'set: ' + t.value;
}})()
'''}, timeout=10)
print(f'  Set: {resp.json().get("result","")}')

time.sleep(2)

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
PYEOF
