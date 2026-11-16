#!/usr/bin/env python3
"""Resume the IG-API-onboarding OAuth chain after Meta's temp block lifts.

Watches the facebook_business_extension tab on the :9229 debug Chrome.
While Meta shows "Temporarily Blocked", reloads every INTERVAL and retries.
Once the block clears, clicks through the remaining wizard steps
(Continue / Allow / Next / Done) and reports the final state.

Run:  nohup python3 ig_onboarding_resume.py >> logs/ig_onboarding_resume.log 2>&1 &
"""
import json, time, urllib.request, sys

CDP = "http://172.22.144.1:9229"
INTERVAL = 600          # seconds between retries while blocked
MAX_TRIES = 18          # ~3h budget
STEP_CLICK = """(()=>{
  const t=document.body.innerText||'';
  if(/temporarily blocked/i.test(t)) return 'BLOCKED';
  const want=[/^(continue|allow|next|done|got it|finish|confirm|authorize|agree)$/i];
  const bs=[...document.querySelectorAll('[role="button"],button,a')]
    .filter(e=>want.some(r=>r.test((e.innerText||'').trim())));
  if(bs.length){bs[bs.length-1].click();return 'clicked:'+(bs[bs.length-1].innerText||'').trim().slice(0,30)}
  return 'idle:'+t.slice(0,120).replace(/\\n/g,' ');
})()"""

def tabs():
    return json.loads(urllib.request.urlopen(CDP + "/json/list", timeout=10).read())

def eval_on(tab_id, expr, mid=[0]):
    import websocket
    mid[0] += 1
    ws = websocket.create_connection(
        next(t["webSocketDebuggerUrl"] for t in tabs() if t.get("id") == tab_id),
        timeout=30, suppress_origin=True)
    ws.send(json.dumps({"id": mid[0], "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    end = time.time() + 25
    while time.time() < end:
        try:
            r = json.loads(ws.recv())
        except Exception:
            ws.close(); return "WS-DIED"
        if r.get("id") == mid[0]:
            ws.close()
            return r.get("result", {}).get("result", {}).get("value")
    ws.close(); return "TIMEOUT"

def main():
    for attempt in range(1, MAX_TRIES + 1):
        try:
            ts = tabs()
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] attempt {attempt}: CDP probe failed: {e} — retrying next interval", flush=True)
            time.sleep(INTERVAL)
            continue
        ext = next((t for t in ts if "facebook_business_extension" in t.get("url", "")), None)
        if not ext:
            # chain finished or tab closed — check the oauth dialog tab
            dlg = next((t for t in ts if "dialog/oauth" in t.get("url", "")), None)
            print(f"[{time.strftime('%H:%M:%S')}] business_extension tab gone; dialog tab url={dlg['url'][:120] if dlg else 'NONE'}", flush=True)
            if dlg:
                txt = eval_on(dlg["id"], "document.body.innerText.slice(0,400)") or ""
                print(f"  dialog text: {txt[:200]}", flush=True)
            return
        url = ext["url"]
        try:
            txt = eval_on(ext["id"], "location.href.slice(0,90)+' ||| '+document.body.innerText.slice(0,300)")
        except Exception as e:
            print(f"  eval failed: {e} — retrying next interval", flush=True)
            time.sleep(INTERVAL)
            continue
        print(f"[{time.strftime('%H:%M:%S')}] attempt {attempt}: {str(txt)[:160]}", flush=True)
        if txt and "Temporarily Blocked" not in str(txt):
            # block cleared — click through wizard steps a few times
            for _ in range(6):
                r = eval_on(ext["id"], STEP_CLICK)
                print(f"  step: {r}", flush=True)
                if not r or "WS-DIED" in str(r) or "TIMEOUT" in str(r):
                    break
                time.sleep(8)
                ts2 = tabs()
                ext2 = next((t for t in ts2 if "facebook_business_extension" in t.get("url", "")), None)
                if not ext2:
                    print("  extension tab closed — handoff done", flush=True)
                    return
                ext = ext2
            return
        # still blocked — reload and wait
        try:
            ws = __import__("websocket").create_connection(ext["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            ws.send(json.dumps({"id": 2, "method": "Page.reload"}))
            ws.close()
        except Exception as e:
            print(f"  reload failed: {e}", flush=True)
        time.sleep(INTERVAL)
    print("gave up after MAX_TRIES", flush=True)

if __name__ == "__main__":
    main()
