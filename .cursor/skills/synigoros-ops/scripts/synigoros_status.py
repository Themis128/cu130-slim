#!/usr/bin/env python3
"""synigoros_status.py — poll the Ombudsman tracking page for a complaint's status.

    synigoros_status.py [--mail EMAIL] [--kpa 202610260029] [--bridge URL]

Fill the tracking form on the adhoc browser bridge, click Ταυτοποίηση,
extract Κατάσταση + Αριθμός Πρωτοκόλλου. Exit 0 with the status line, 1 on
identification failure / bridge error. Designed to run inside social-api
(BRIDGE=http://browser-novnc-adhoc:9224) or anywhere the bridge is reachable.
"""
import json, sys, time, urllib.request, urllib.error

BRIDGE = "http://browser-novnc-adhoc:9224"
TRACK = "https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas"
MAIL  = "baltzakis.themis@gmail.com"   # the complaint lives under this account
KPA   = "202610260029"

def call(path, data, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(f"{BRIDGE}{path}",
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=40).read())
        except urllib.error.HTTPError as e:
            body = e.read()[:150]
            if (e.code == 409 or (e.code == 500 and b"context was destroyed" in body)) and i < tries - 1:
                time.sleep(15); continue
            raise SystemExit(f"bridge {e.code}: {body}")

def ev(expr):
    return call("/session/evaluate", {"expression": expr}).get("result")

def main() -> int:
    global BRIDGE, MAIL, KPA
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--bridge": BRIDGE = args[i+1]
        if a == "--mail":   MAIL   = args[i+1]
        if a == "--kpa":    KPA    = args[i+1]

    # any platform arg just boots the browser — navigate handles the real URL
    call("/session/start", {"platform": "reddit", "interactive": False})
    time.sleep(4)
    call("/session/navigate", {"url": TRACK})
    for _ in range(10):                      # wait for the tracking form
        if ev("!!document.getElementById('webMail')") or \
           ev("document.body.innerText.includes('Κατάσταση')"):
            break
        time.sleep(3)
    else:
        raise SystemExit("tracking page never rendered")
    if ev("!!document.getElementById('webMail')"):   # fresh session → identify
        ev(f"(()=>{{const m=document.getElementById('webMail');m.value='{MAIL}';"
           f"m.dispatchEvent(new Event('input',{{bubbles:true}}));"
           f"const c=document.getElementById('webCode');c.value='{KPA}';"
           f"c.dispatchEvent(new Event('input',{{bubbles:true}}));return 1}})()")
        call("/session/click", {"selector": "button"})
        time.sleep(14)
        if ev("document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
            print("IDENTIFY FAILED — wrong mail/ΚΠΑ pair")
            return 1
    out = ev("""(()=>{const t=document.body.innerText;
      const grab=(re)=>{const m=t.match(re);return m?m[1].trim():''};
      return JSON.stringify({
        status: grab(/Κατάσταση\\s*\\n([^\\n]+)/) || grab(/(Αναμονή[^\\n]*|Επεξεργασία[^\\n]*|Ολοκληρώθηκε[^\\n]*)/),
        proto:  grab(/Αριθμός Πρωτοκόλλου\\s*\\n?\\s*(\\d+)/),
        kpa:    grab(/Κωδικός Παρακολούθησης Αναφοράς\\s*\\n?\\s*(\\d+)/)
      })})()""")
    print(out)
    return 0

if __name__ == "__main__":
    sys.exit(main())
