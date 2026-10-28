#!/usr/bin/env python3
"""synigoros_status.py — poll the Ombudsman tracking page for a complaint's status.

    synigoros_status.py [--mail EMAIL] [--kpa KPA] [--bridge URL]

Fill the tracking form on the adhoc browser bridge, click Ταυτοποίηση,
extract Κατάσταση + Αριθμός Πρωτοκόλλου. Exit 0 with the status line, 1 on
identification failure / bridge error / empty status. Designed to run
inside social-api (BRIDGE=http://browser-novnc-adhoc:9224) or anywhere
the bridge is reachable.

Credentials come from SYNIGOROS_MAIL + SYNIGOROS_KPA (or the --mail/--kpa
flags) — nothing identifying is committed.
"""
import json, os, sys, time, urllib.request, urllib.error

BRIDGE = "http://browser-novnc-adhoc:9224"
TRACK = "https://www.synigoros.gr/el/anafora/parakolouthisi-anaforas"
MAIL  = os.environ.get("SYNIGOROS_MAIL", "")
KPA   = os.environ.get("SYNIGOROS_KPA", "")

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
    if not MAIL or not KPA:
        raise SystemExit("need SYNIGOROS_MAIL + SYNIGOROS_KPA (env or --mail/--kpa)")

    # any platform arg just boots the browser — navigate handles the real URL
    call("/session/start", {"platform": "reddit", "interactive": False})
    try:
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
            ev("(()=>{const m=document.getElementById('webMail');m.value=%s;"
               "m.dispatchEvent(new Event('input',{bubbles:true}));"
               "const c=document.getElementById('webCode');c.value=%s;"
               "c.dispatchEvent(new Event('input',{bubbles:true}));return 1})()"
               % (json.dumps(MAIL), json.dumps(KPA)))
            call("/session/click", {"selector": "button"})
            # bounded poll: wait for the status view or the explicit
            # failure text — a fixed sleep returns empty success on slow
            # identification
            failed = False
            for _ in range(10):
                time.sleep(3)
                if ev("document.body.innerText.includes('Κατάσταση')"):
                    break
                if ev("document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
                    failed = True
                    break
            if failed or ev("document.body.innerText.includes('Δεν έγινε ταυτοποίηση')"):
                print("IDENTIFY FAILED — wrong mail/ΚΠΑ pair")
                return 1
        out = ev("""(()=>{const t=document.body.innerText;
          const grab=(re)=>{const m=t.match(re);return m?m[1].trim():''};
          return JSON.stringify({
            status: grab(/Κατάσταση\\s*\\n([^\\n]+)/) || grab(/(Αναμονή[^\\n]*|Επεξεργασία[^\\n]*|Ολοκληρώθηκε[^\\n]*)/),
            proto:  grab(/Αριθμός Πρωτοκόλλου\\s*\\n?\\s*(\\d+)/),
            kpa:    grab(/Κωδικός Παρακολούθησης Αναφοράς\\s*\\n?\\s*(\\d+)/)
          })})()""")
        data = json.loads(out)
        # a cached session may show a DIFFERENT complaint's status — the
        # form never ran, so the displayed ΚΠΑ must match the requested one
        if data.get("kpa") and data["kpa"] != KPA:
            print(out)
            print(f"WRONG-COMPLAINT — page shows ΚΠΑ {data['kpa']}, requested {KPA} "
                  "(cached session; reset required)")
            return 1
        # an empty status means the page left the form in an unexpected
        # state (verification challenge, slow transition) — not a success
        if not data.get("status"):
            print(out)
            print("INCOMPLETE — status empty (challenge or transition); retry later")
            return 1
        print(out)
        return 0
    finally:
        # release the pinned bridge session — a held session 409s every
        # other platform for five minutes
        try: call("/session/close", {})
        except Exception: pass

if __name__ == "__main__":
    sys.exit(main())
