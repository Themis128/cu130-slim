#!/usr/bin/env python3
"""li_connect.py — send LinkedIn connection requests via the debug Chrome CDP lane.

Drives the LIVE Voyager endpoint inside a logged-in linkedin.com tab on the
Windows chrome-debug-profile (CDP :9229). The dead /growth/normInvitations
path is NOT used.

Usage:
  python li_connect.py status
  python li_connect.py pending                          # sent + received invites
  python li_connect.py connect <profile-url> [...]      # send requests
  python li_connect.py connect <url> --note "..."       # with note
  python li_connect.py connect <url> --dry-run          # resolve + report only
  python li_connect.py withdraw "Name Substring" [...]  # withdraw sent invites
  python li_connect.py <cmd> --cdp http://192.168.1.23:9229
"""
import argparse
import json
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

try:
    import websocket  # websocket-client
except ImportError:
    sys.exit("pip install websocket-client")

LI_ORIGIN = "https://www.linkedin.com"
PROFILE_DECORATION = "com.linkedin.voyager.dash.deco.identity.profile.WebTopCardCore-16"
INVITE_PATH = "/voyager/api/voyagerRelationshipsDashMemberRelationships?action=verifyQuotaAndCreateV2"


# ---------- CDP plumbing ----------

def local_ipv4s():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        out = __import__("subprocess").check_output(["ipconfig"], text=True, timeout=10)
        ips.update(re.findall(r"IPv4[^:]*:\s*(\d+\.\d+\.\d+\.\d+)", out))
    except Exception:
        pass
    return sorted(ips)


def find_cdp(explicit=None):
    cands = []
    if explicit:
        cands.append(explicit.rstrip("/"))
    cands += ["http://localhost:9229", "http://127.0.0.1:9229"]
    for ip in local_ipv4s():
        cands.append(f"http://{ip}:9229")
    for base in cands:
        try:
            with urllib.request.urlopen(base + "/json/version", timeout=3) as r:
                if "Chrome" in r.read(4096).decode("utf-8", "replace"):
                    return base
        except Exception:
            continue
    sys.exit("No CDP endpoint on :9229 found (is chrome-debug-profile running?)")


def http_json(base, path, method="GET"):
    req = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


class CDP:
    def __init__(self, base):
        self.base = base
        self.mid = 0
        self.ws = None
        self.tid = None

    def targets(self):
        return http_json(self.base, "/json")

    def li_tab_id(self):
        pages = [t for t in self.targets()
                 if t.get("type") == "page" and "linkedin.com" in t.get("url", "")]
        return pages[-1]["id"] if pages else None

    def ensure_tab(self):
        tid = self.li_tab_id()
        if tid is None:
            # create a tab then send it to linkedin
            req = urllib.request.Request(
                self.base + "/json/new?" + urllib.parse.quote(LI_ORIGIN + "/feed/", safe=""),
                method="PUT")
            t = json.loads(urllib.request.urlopen(req, timeout=10).read())
            tid = t["id"]
            time.sleep(8)
        self.attach(tid)
        return self

    def attach(self, tid):
        for t in self.targets():
            if t["id"] == tid:
                self.ws = websocket.create_connection(
                    t["webSocketDebuggerUrl"], suppress_origin=True, timeout=60)
                self.tid = tid
                return
        raise RuntimeError("tab vanished")

    def reattach(self):
        try:
            self.ws.close()
        except Exception:
            pass
        tid = self.li_tab_id() or self.tid
        self.attach(tid)

    def cmd(self, method, params=None):
        self.mid += 1
        mid = self.mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                return m

    def ev(self, expr, await_promise=True):
        r = self.cmd("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        res = r.get("result", {}).get("result", {})
        if "value" in res:
            return res["value"]
        return {"__exception__": res}

    def nav(self, url, wait=7):
        try:
            self.cmd("Page.enable")
            self.cmd("Page.navigate", {"url": url})
        except Exception:
            pass
        time.sleep(wait)
        self.reattach()


# ---------- LinkedIn in-page helpers ----------

FETCH_JS = """
async (path, opts) => {
  const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)/)||[])[1]||'';
  const headers = Object.assign({'csrf-token': csrf,
    'x-restli-protocol-version': '2.0.0'}, (opts&&opts.headers)||{});
  if (opts && opts.body) headers['content-type'] = 'application/json';
  const r = await fetch(path, {method:(opts&&opts.method)||'GET',
    headers, credentials:'include',
    body: opts && opts.body ? JSON.stringify(opts.body) : undefined});
  const text = await r.text();
  let data=null; try{data=JSON.parse(text)}catch(e){}
  return {status:r.status, data, text:text.slice(0,2000)};
}
"""


def li_fetch(cdp, path, method="GET", body=None):
    expr = f"({FETCH_JS})({json.dumps(path)}, {json.dumps({'method': method, 'body': body})})"
    return cdp.ev(expr)


def slug_from_url(url):
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url)
    return m.group(1) if m else None


def resolve_profile(cdp, slug):
    path = ("/voyager/api/identity/dash/profiles?q=memberIdentity"
            f"&memberIdentity={urllib.parse.quote(slug)}"
            f"&decorationId={urllib.parse.quote(PROFILE_DECORATION)}")
    r = li_fetch(cdp, path)
    if not isinstance(r, dict) or r.get("status") != 200:
        return {"ok": False, "error": f"resolve http {r.get('status') if isinstance(r,dict) else r}"}
    data = r.get("data") or {}
    prof = None
    # live shape: data.elements[] with entityUrn + memberRelationship
    elements = data.get("elements") or []
    if elements:
        prof = elements[0]
    else:
        included = data.get("included") or []
        for it in included:
            urn = it.get("entityUrn") or ""
            if "fsd_profile" in urn:
                prof = it
                break
        if not prof and included:
            prof = included[0]
    if not prof:
        return {"ok": False, "error": "no profile in response", "raw": str(data)[:400]}
    rel = ((prof.get("memberRelationship") or {}).get("memberRelationshipUnion") or {})
    dist = None
    inv_state = None
    inv_detail = None
    for k, v in rel.items():
        if isinstance(v, dict):
            if "memberDistance" in v:
                dist = v.get("memberDistance")
            iu = v.get("invitationUnion") or {}
            for ik, iv in iu.items():
                if ik == "noInvitation":
                    continue
                inv_state = ik
                if isinstance(iv, dict):
                    inv_detail = iv.get("invitationState") or iv.get("state")
    # WITHDRAWN/DECLINED/EXPIRED invites don't block a fresh request
    if inv_detail and str(inv_detail).upper() in ("WITHDRAWN", "DECLINED", "EXPIRED", "CANCELLED"):
        inv_state = None
        inv_detail = None
    return {
        "ok": True,
        "urn": prof.get("entityUrn"),
        "name": ((prof.get("firstName") or "") + " " + (prof.get("lastName") or "")).strip(),
        "distance": dist,
        "invitation_state": inv_state,
        "invitation_detail": inv_detail,
        "headline": (prof.get("headline") or "")[:120],
    }


def classify_invite(r):
    """Return (status_str, detail). status in ok/duplicate/pending/quota/restricted/failed."""
    if not isinstance(r, dict):
        return "failed", str(r)[:200]
    status = r.get("status")
    data = r.get("data")
    text = (r.get("text") or "")
    blob = json.dumps(data)[:4000] if data else text
    low = blob.lower()
    for key, label in [
        ("alreadyinvited", "pending"), ("invitationalready", "pending"),
        ("pendinginvitation", "pending"), ("quota", "quota_exhausted"),
        ("restricted", "restricted"), ("notallowed", "not_allowed"),
        ("alreadyconnected", "already_connected"),
    ]:
        if key in low:
            return label, blob[:300]
    # nested graphql errors in a 200
    if isinstance(data, dict):
        errs = []
        def walk(o):
            if isinstance(o, dict):
                if "errors" in o and isinstance(o["errors"], list) and o["errors"]:
                    errs.extend(o["errors"])
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data)
        if errs:
            return "failed", json.dumps(errs)[:400]
    if status in (200, 201):
        return "ok", blob[:300]
    return "failed", f"http {status}: {blob[:300]}"


def send_invite(cdp, urn, note=None):
    body = {"invitee": {"inviteeUnion": {"memberProfile": urn}}}
    if note:
        body["customMessage"] = note
        body["invitee"]["customMessage"] = note
    r = li_fetch(cdp, INVITE_PATH, method="POST", body=body)
    st, detail = classify_invite(r)
    if st == "failed" and note:
        # retry once without note — customMessage shape is unverified
        body.pop("customMessage", None)
        body["invitee"].pop("customMessage", None)
        r2 = li_fetch(cdp, INVITE_PATH, method="POST", body=body)
        st2, d2 = classify_invite(r2)
        if st2 != "failed":
            return st2, d2 + " (note dropped)"
    return st, detail


INVITE_CARDS_JS = """
JSON.stringify([...document.querySelectorAll('main a[href*="/in/"]')].map(a=>{
  let card=a;
  for(let i=0;i<8&&card;i++){card=card.parentElement;
    if(card&&(card.innerText||'').length<900&&(card.innerText||'').includes((a.innerText||'').trim().split(' ')[0])) break;}
  return {href:a.href.split('?')[0], text:(card?card.innerText:'').replace(/\\s+/g,' ').slice(0,300)};
}).filter((v,i,arr)=>arr.findIndex(x=>x.href===v.href)===i))
"""


def scrape_invites(cdp, which):
    cdp.nav(LI_ORIGIN + "/mynetwork/invitation-manager/" + ("sent/" if which == "sent" else ""), wait=9)
    cdp.ev("window.scrollTo(0,document.body.scrollHeight)")
    time.sleep(2)
    try:
        cdp.reattach()
    except Exception:
        pass
    raw = cdp.ev(INVITE_CARDS_JS)
    try:
        cards = json.loads(raw) if isinstance(raw, str) else []
    except Exception:
        cards = []
    seen, out = set(), []
    for c in cards:
        if c["href"] in seen:
            continue
        seen.add(c["href"])
        out.append(c)
    return out


def cmd_pending(cdp):
    for which in ("received", "sent"):
        print(f"=== {which.upper()} ===")
        cards = scrape_invites(cdp, which)
        if not cards:
            print("(none)")
        for c in cards:
            print(f"- {c['text'][:200]}\n  {c['href']}")
        print()
    return 0


WITHDRAW_BY_SLUG_JS = """
(()=>{
  const spans=[...document.querySelectorAll('span')].filter(e=>e.children.length===0&&(e.innerText||'').trim()==='Withdraw');
  for(const s of spans){
    let card=s.parentElement,slug=null;
    for(let i=0;i<12&&card;i++){
      const a=card.querySelector('a[href*="/in/"]');
      if(a){slug=(a.href.split('/in/')[1]||'').split('/')[0].split('?')[0];break;}
      card=card.parentElement;
    }
    if(slug===SLUG){
      const btn=s.closest('button,[role="button"],a')||s.parentElement;
      btn.click();
      return 'clicked:'+slug;
    }
  }
  return 'not-found';
})()
"""


def cmd_withdraw(cdp, targets):
    """Withdraw sent invites. Each target is an /in/ profile URL or slug —
    NEVER a display name (DOM name matching withdrew the wrong card once)."""
    cdp.nav(LI_ORIGIN + "/mynetwork/invitation-manager/sent/", wait=9)
    cdp.ev("window.scrollTo(0,document.body.scrollHeight)")
    time.sleep(2)
    try:
        cdp.reattach()
    except Exception:
        pass
    for target in targets:
        slug = slug_from_url(target) or target.strip("/").split("/")[-1]
        res = cdp.ev(WITHDRAW_BY_SLUG_JS.replace("SLUG", json.dumps(slug)))
        print(f"{slug}: {res}")
        if "clicked" not in str(res):
            continue
        time.sleep(2)
        try:
            cdp.reattach()
        except Exception:
            pass
        conf = cdp.ev("""(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^withdraw$/i.test((x.innerText||'').trim())); if(b){b.click(); return 'confirmed'} return 'no-dialog'})()""")
        print(f"  dialog: {conf}")
        time.sleep(3)
        cdp.nav(LI_ORIGIN + "/mynetwork/invitation-manager/sent/", wait=7)
        try:
            cdp.reattach()
        except Exception:
            pass
    return 0


def cmd_status(cdp):
    tid = cdp.li_tab_id()
    if not tid:
        print("no linkedin.com tab — run `connect` with a URL to create one")
        return 1
    cdp.attach(tid)
    me = li_fetch(cdp, "/voyager/api/me")
    logged = isinstance(me, dict) and me.get("status") == 200
    print(f"cdp={cdp.base} tab={tid}")
    print(f"linkedin session: {'LOGGED IN' if logged else 'NOT LOGGED IN'}")
    if logged:
        mini = (me.get("data") or {}).get("miniProfile") or (me.get("data") or {})
        print("profile:", mini.get("firstName", ""), mini.get("lastName", ""))
    return 0


def cmd_connect(cdp, urls, note, dry_run):
    results = []
    first = True
    for url in urls:
        slug = slug_from_url(url)
        if not slug:
            results.append((url, "failed", "not a /in/ profile url"))
            continue
        if first:
            cdp.nav(LI_ORIGIN + "/feed/", wait=5)
            first = False
        else:
            cdp.nav(LI_ORIGIN + "/in/" + slug + "/", wait=6)
        prof = resolve_profile(cdp, slug)
        if not prof.get("ok"):
            results.append((url, "failed", prof.get("error")))
            continue
        dist = prof.get("distance")
        inv_state = prof.get("invitation_state")
        if dist == "DISTANCE_1":
            results.append((url, "already_connected", prof.get("name")))
            continue
        if inv_state:
            results.append((url, "pending", f"{prof.get('name')} inv={inv_state}"))
            continue
        if dry_run:
            results.append((url, "dry_run", f"{prof.get('name')} dist={dist} urn={prof.get('urn')}"))
            continue
        st, detail = send_invite(cdp, prof["urn"], note)
        results.append((url, st, f"{prof.get('name')} | {detail[:160]}"))
        time.sleep(6 + (hash(url) % 5))  # gentle pacing 6-10s
    for url, st, detail in results:
        print(f"[{st:>18}] {url}\n{'':>20}{detail}")
    return 0 if all(s in ("ok", "already_connected", "dry_run", "pending") for _, s, _ in results) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["status", "connect", "pending", "withdraw"])
    ap.add_argument("urls", nargs="*")
    ap.add_argument("--note", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cdp", default=None)
    args = ap.parse_args()
    base = find_cdp(args.cdp)
    cdp = CDP(base).ensure_tab()
    if args.cmd == "status":
        return cmd_status(cdp)
    if args.cmd == "pending":
        return cmd_pending(cdp)
    if args.cmd == "withdraw":
        if not args.urls:
            ap.error("withdraw needs at least one profile URL or /in/ slug")
        return cmd_withdraw(cdp, args.urls)
    if not args.urls:
        ap.error("connect needs at least one profile URL")
    return cmd_connect(cdp, args.urls, args.note, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
