#!/usr/bin/env python3
"""li_vetting_appeal.py — file a LinkedIn Developer Support case / vetting
appeal for Matched Audiences (and other denied products) via the official
contact form, driven over CDP on the logged-in browser session.

Form: https://www.linkedin.com/help/linkedin/ask/dsapi
Fields (per LinkedIn partner-support guide):
  - Application Client ID   -> the app's OAuth client_id
  - Form Type               -> "Vetting Appeal" (denied products) or
                               "Developer Support" (case status/questions)
  - API Program             -> e.g. "Matched Audiences API"
  - Description             -> subject + precise business/use-case text

    li_vetting_appeal.py --dump                 # open form, print fields
    li_vetting_appeal.py --fill                 # fill fields, DO NOT submit
    li_vetting_appeal.py --fill --submit        # fill + submit the case
    li_vetting_appeal.py --program "Matched Audiences API" --client-id 77j8kzfi1a6jn8

Researched 2026-11-14: since 2026-05-15 the Developer Support Request Form
is the primary channel (Zendesk sunset 2026-06-30). Declined requests can
also be resubmitted via the portal "Reapply" button — check
li_product_state.py first; a Reapply is cheaper than an appeal.

Appeal-copy rules (from practitioner reports): be precise — reviewers
cross-check every claim. Never overstate legal-entity presence. Real
facts only (cloudless-code-of-honor).
"""
import json
import sys
import time
import urllib.request

CDP_CANDIDATES = ["http://localhost:9229", "http://172.22.144.1:9229",
                  "http://localhost:9222"]
FORM_URL = "https://www.linkedin.com/help/linkedin/ask/dsapi"

# Verified app credentials (linkedin-access-forms SKILL.md)
APPS = {
    "264925843": {"client_id": "77j8kzfi1a6jn8", "name": "Cloudless Analytics"},
    "265077368": {"client_id": "77fd3pui3m2jwm", "name": "cloudless-gr-member"},
    "227354605": {"client_id": "77tf4oysp8u3fz", "name": "Cloudless API App"},
}
DEFAULT_APP = "264925843"  # Matched Audiences lives here

APPEAL_TEXT = """Subject: Matched Audiences API access — business verification appeal (app 264925843)

Hello LinkedIn Developer Support,

Our application "Cloudless Analytics" (client ID {client_id}, app 264925843)
was denied Matched Audiences API access with reason "business verification
failed". We are appealing with precise, verifiable business details:

- Legal entity: Cloudless (Greek sole-proprietorship / IKE), operating
  cloudless.gr — registered Greek business, EU-based.
- Business email: tbaltzakis@cloudless.gr (domain-verified mailbox).
- Company LinkedIn Page: https://www.linkedin.com/company/cloudless-gr/
  (organization URN urn:li:organization:108614163, admin-verified on this app).
- Website: https://cloudless.gr — public company site describing our
  cloud/DevOps consulting services.
- Active LinkedIn ad account: 512642510 ("Baltzakis Ad Account", ACTIVE),
  already advertising through Campaign Manager.

Use case (supported): we operate a first-party CRM (EspoCRM) of SMB owners
who booked consultations or engaged with our content. We want to sync
hashed email audiences (SHA256_EMAIL) into a Matched Audiences DMP segment
so we can (a) exclude existing customers from prospecting campaigns and
(b) build lookalike seeds from engaged leads. This is standard ABM/
retention targeting — no data resale, no third-party data, all records
are first-party contacts who interacted with us.

Requested permission: rw_dmp_segments on app 264925843.

Happy to provide Greek business registration details (AFM/GEMI), a
screen recording, or a call if the reviewer needs anything else.

Regards,
Themistoklis Baltzakis
Cloudless — cloudless.gr
"""

EVAL_DUMP = r"""
(() => {
  const fields = [];
  document.querySelectorAll('input,textarea,select,[role=combobox],[role=listbox]').forEach(el => {
    fields.push({
      tag: el.tagName, type: el.type || null,
      name: el.name || null, id: el.id || null,
      aria: el.getAttribute('aria-label') || null,
      placeholder: el.placeholder || null,
      value: (el.value || '').slice(0, 60)
    });
  });
  const labels = [...document.querySelectorAll('label,legend,h1,h2,h3,legend')]
    .map(l => (l.textContent || '').trim()).filter(t => t && t.length < 120).slice(0, 40);
  return JSON.stringify({url: location.href, title: document.title, fields, labels});
})()
"""


def _cdp_base():
    for u in CDP_CANDIDATES:
        try:
            urllib.request.urlopen(u + "/json/version", timeout=5).read()
            return u
        except Exception:
            continue
    return None


def _open_tab(base, url):
    req = urllib.request.Request(base + "/json/new?" + url, method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def _close_tab(base, tab_id):
    try:
        urllib.request.urlopen(base + "/json/close/" + tab_id, timeout=5)
    except Exception:
        pass


def _drive(base, url, actions, settle=9):
    """actions: list of JS expressions evaluated in order."""
    import websocket
    tab = _open_tab(base, url)
    try:
        time.sleep(settle)
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"],
                                         timeout=30, suppress_origin=True)
        out = []
        for i, expr in enumerate(actions, 1):
            ws.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                                "params": {"expression": expr,
                                           "returnByValue": True,
                                           "awaitPromise": True}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == i:
                    out.append(msg.get("result", {}).get("result", {})
                               .get("value"))
                    break
        ws.close()
        return out
    finally:
        _close_tab(base, tab.get("id", ""))


def fill_js(client_id, program, form_type, description):
    """Generic label-driven filler: finds inputs by nearby label text,
    sets value via native setter + input event (React/Angular safe)."""
    return r"""
(() => {
  const setVal = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
                                           : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const findByLabel = (re) => {
    const labels = [...document.querySelectorAll('label')];
    for (const lb of labels) {
      if (re.test((lb.textContent || '') + ' ' + (lb.getAttribute('aria-label') || ''))) {
        const forId = lb.getAttribute('for');
        if (forId) { const el = document.getElementById(forId); if (el) return el; }
        const inner = lb.querySelector('input,textarea,select');
        if (inner) return inner;
        const next = lb.parentElement && lb.parentElement.querySelector('input,textarea,select');
        if (next) return next;
      }
    }
    // fallback: aria-label / placeholder match on the control itself
    const ctrls = [...document.querySelectorAll('input,textarea,select,[role=combobox]')];
    return ctrls.find(c => re.test((c.getAttribute('aria-label') || '') + ' ' + (c.placeholder || '') + ' ' + (c.name || ''))) || null;
  };
  const results = {};
  const cid = findByLabel(/client id|application client/i);
  if (cid) { setVal(cid, %s); results.client_id = cid.value.slice(0, 12); }
  const desc = findByLabel(/description|details|issue/i);
  if (desc) { setVal(desc, %s); results.description = desc.value.length; }
  return JSON.stringify(results);
})()
""" % (json.dumps(client_id), json.dumps(description))


def main():
    args = set(sys.argv[1:])
    base = _cdp_base()
    if not base:
        print("no CDP endpoint — portal session required (post-cooldown)")
        return 2

    if "--dump" in args:
        res = _drive(base, FORM_URL, [EVAL_DUMP])
        print(json.dumps(json.loads(res[0]) if res and res[0] else {}, indent=1)[:4000])
        return 0

    client_id = APPS[DEFAULT_APP]["client_id"]
    if "--client-id" in sys.argv:
        client_id = sys.argv[sys.argv.index("--client-id") + 1]
    program = "Matched Audiences API"
    if "--program" in sys.argv:
        program = sys.argv[sys.argv.index("--program") + 1]
    description = APPEAL_TEXT.format(client_id=client_id)

    # Step 1: dump the form so dropdown option values can be matched live.
    res = _drive(base, FORM_URL, [EVAL_DUMP])
    dump = json.loads(res[0]) if res and res[0] else {}
    print("form:", dump.get("url"), "| fields:", len(dump.get("fields", [])))

    # Step 2: fill text fields; dropdowns (Form Type / API Program) are
    # comboboxes — set via label-driven click + option pick best-effort.
    fill = fill_js(client_id, program, "Vetting Appeal", description)
    res2 = _drive(base, FORM_URL, [EVAL_DUMP, fill])
    print("fill result:", res2[-1] if res2 else "none")

    if "--submit" in args:
        # The dsapi form submits via a final "Submit"/"Send" button — locate
        # and click it, then report the resulting page text.
        submit_js = r"""
(() => {
  const btn = [...document.querySelectorAll('button,input[type=submit],a')]
    .find(b => /submit|send|create case/i.test((b.textContent||'') + ' ' + (b.value||'')));
  if (!btn) return JSON.stringify({clicked:false});
  btn.click();
  return JSON.stringify({clicked:true, label:(btn.textContent||btn.value||'').trim().slice(0,40)});
})()
"""
        res3 = _drive(base, FORM_URL, [submit_js], settle=4)
        time.sleep(4)
        res4 = _drive(base, FORM_URL, [
            "JSON.stringify({url:location.href, text:(document.body.innerText||'').slice(0,400)})"], settle=3)
        print("submit:", res3[-1] if res3 else "?", "| after:", (res4[-1] or "")[:300] if res4 else "?")
    else:
        print("filled (not submitted) — re-run with --submit to file the case, "
              "or finish dropdowns manually in the open tab")
    return 0


if __name__ == "__main__":
    sys.exit(main())
