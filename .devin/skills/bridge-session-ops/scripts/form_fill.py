#!/usr/bin/env python3
"""form_fill.py — drive any web form through the shared bridge.
Pin → navigate → wait for fields → fill a field-map → submit.

    form_fill.py '<url>' '<field_map_json>' [submit_selector]

field_map_json maps selector->value:
  {"#email": "me@x.com", "[name='company']": "Cloudless"}
submit_selector defaults to "button[type=submit],input[type=submit]".
Handles 409 shared-session contention with backoff; pins keepawake.
"""
import json
import sys
import time
import urllib.request
import urllib.error

B = "http://browser-novnc:9223"

def pin() -> None:
    try:
        urllib.request.urlopen(urllib.request.Request(
            "http://stack-ops:8787/keepawake/browser-novnc?ttl=900",
            method="POST", data=b""), timeout=8)
    except Exception:
        pass

def ev(expr: str, tries: int = 10) -> dict:
    for i in range(tries):
        r = urllib.request.Request(f"{B}/session/evaluate",
            data=json.dumps({"expression": expr}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=25) as f:
                return json.loads(f.read())
        except urllib.error.HTTPError as e:
            if e.code != 409:
                return {"error": str(e)}
            time.sleep(8 + i * 7)
    return {"error": "busy"}


def fill(fields: dict) -> dict:
    results = {}
    for sel, val in fields.items():
        js = (
            "(()=>{const e=document.querySelector(" + json.dumps(sel) + ");"
            "if(!e)return 'MISS:"+json.dumps(sel)+"';"
            "e.focus();e.value=" + json.dumps(val) + ";"
            "e.dispatchEvent(new Event('input',{bubbles:true}));"
            "e.dispatchEvent(new Event('change',{bubbles:true}));return 'ok'})()"
        )
        r = ev(js)
        results[sel] = r.get("result", r.get("error", "?"))
    return results


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__); return 1
    url, fields = sys.argv[1], json.loads(sys.argv[2])
    submit = sys.argv[3] if len(sys.argv) > 3 else "button[type=submit],input[type=submit]"
    pin()
    ev(f"location.href={json.dumps(url)}")
    time.sleep(8)
    # wait for first field to appear
    first = next(iter(fields))
    for _ in range(10):
        r = ev(f"!!document.querySelector({json.dumps(first)})")
        if r.get("result"):
            break
        time.sleep(3)
    res = fill(fields)
    print(json.dumps(res, indent=2))
    if all(v == "ok" for v in res.values()):
        r = ev(f"document.querySelector({json.dumps(submit)})?.click()")
        print("submit:", str(r.get("result") or r.get("error"))[:100])
    return 0


if __name__ == "__main__":
    sys.exit(main())
