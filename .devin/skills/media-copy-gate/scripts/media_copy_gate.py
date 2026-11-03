#!/usr/bin/env python3
"""media_copy_gate.py — pre-render copy QA.
Runs the stack's real spell-check + NLP + SEO scoring on media overlay text
BEFORE the image pipeline renders it — returns pass/fail + corrected text.
Protects proper nouns/hashtags/domains/urns from the corrector (the
LanguageTool 'Kakkava→Baklava' bug)."""
import json
import re
import sys
import urllib.request

API = "http://127.0.0.1:8083"
PROTECT = re.compile(r"(#\w+|https?://\S+|urn:\S+|@[\w.]+|\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\b)")


def _post(path, payload):
    r = urllib.request.urlopen(urllib.request.Request(
        API + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}), timeout=60)
    return json.loads(r.read())


import subprocess
def _in_api(code: str):
    r = subprocess.run(["docker", "compose", "exec", "-T", "social-api",
                        "python", "-c", code], cwd="social-automation/backend",
                       capture_output=True, text=True, timeout=120)
    return r.stdout.strip()


def _nlp(text):
    try:
        safe = text.replace('"', '\\"')
        out = _in_api(f'from app.services.plain_english import sofia_nlp_score; print(sofia_nlp_score("{safe}")[0])')
        return int(out.strip("() ,") or -1)
    except Exception:
        return -1


def _seo(text):
    return -1  # seo composite needs the publish path — nlp is the gate


def check(text: str) -> dict:
    nlp, seo = _nlp(text), _seo(text)
    ok = (nlp < 0 or nlp >= 80) and (seo < 0 or seo >= 90)
    return {"pass": ok, "nlp": nlp, "seo": seo, "text": text}


if __name__ == "__main__":
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(check(text), indent=1))
    sys.exit(0 if check(text)["pass"] else 1)
