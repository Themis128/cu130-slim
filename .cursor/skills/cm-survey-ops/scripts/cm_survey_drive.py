#!/usr/bin/env python3
"""cm_survey_drive.py — fill + submit the LinkedIn CM Standard-tier
Qualtrics access-request form over CDP, end to end.

Verified live against SV_3Q1SbLSZoImyw6O (2026-11-12). This JFE theme:

  - MC questions commit via the engine API:
      q = Qualtrics.SurveyEngine.QuestionData.getInstance('QID9')
      q.setChoiceValue('<choiceId>', true)   # ids are 1-based strings
      q.getChoiceValue('<id>')               # verify
    — DOM clicks do NOT commit on this theme.
  - TE (text entry) questions have NO setTextValue on this version —
    write via native value setter + input/change/blur events inside
    #question-<qid>; the response is harvested at nav time.
  - Navigation = the real #next-button (.navigation-button). NEVER the
    "Go to next issue" banner — it matches /next/i but is a chat widget.
  - Multi-round: conditional-required questions only flag errors AFTER a
    failed Next, so loop fill->next->rescan until Thank-you (max 5).
  - Q_DL binding: the form must be opened via the app's own products-page
    link or the submission orphans. find_form_url() scrapes it from
    developers.linkedin.com app 264925843; falls back to the bare URL.

    cm_survey_drive.py <mp4> [--dry-run] [--form-url URL] [--no-submit]

The screen-recording field wants a LINK — auto-creates a Nextcloud
public share of the mp4 (WebDAV PUT + OCS share) unless CM_RECORDING_URL
is set. Registered address via CM_STREET/CM_CITY/CM_REGION/CM_ZIP.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

CDP_CANDIDATES = ["http://localhost:9229", "http://172.22.144.1:9229",
                  "http://localhost:9222"]
DEFAULT_FORM = ("https://linkedincustomerops.qualtrics.com/jfe/form/"
                "SV_3Q1SbLSZoImyw6O")
APP_PRODUCTS = "https://www.linkedin.com/developers/apps/264925843/products"
MAX_ROUNDS = 5
ENV_FILE = "/home/tbaltzakis/cu130-slim/.env"

ADDRESS = {
    "street": os.environ.get("CM_STREET", "Thermopilon 15"),
    "city": os.environ.get("CM_CITY", "Koropi"),
    "region": os.environ.get("CM_REGION", "Attica"),
    "zip": os.environ.get("CM_ZIP", "19441"),
}

ABOUT_BUSINESS = (
    "Cloudless provides managed cloud and automation for SMBs. SocialAuto "
    "is our internal product managing our company's LinkedIn presence — "
    "Company Page publishing, comment and reaction monitoring, and "
    "engagement tracking that routes leads into our audit funnel.")
DETAILED_DESC = (
    "Page management: automated company post scheduling and comment "
    "monitoring on our own page. Page analytics: follower and post stats "
    "feed our insights pipeline for content decisions. Profile management: "
    "scheduled posts to the founder's personal profile under the same "
    "editorial process.")
INDUSTRIES = "Professional services, e-commerce, SMB owners"
PRIVACY_URL = "https://cloudless.gr/privacy"
TEST_LOGIN = os.environ.get(
    "CM_TEST_LOGIN",
    "read-only test account at https://social.cloudless.gr")

# TE questions (verified QIDs on SV_3Q1SbLSZoImyw6O)
TEXT = {
    "QID1": "Themis Baltzakis",     # Organization's Legal Name
    "QID45": "https://cloudless.gr",  # Organization Website
    "QID5": ADDRESS["street"],
    "QID6": ADDRESS["city"],
    "QID8": ADDRESS["region"],
    "QID46": ADDRESS["zip"],
    "QID26": ABOUT_BUSINESS,
    "QID15": DETAILED_DESC,
    "QID33": INDUSTRIES,
    "QID35": PRIVACY_URL,
    "QID43": "__RECORDING_URL__",
    "QID44": TEST_LOGIN,
}
# MC questions -> choice IDs (verified against live Choices maps)
CHOICE = {
    "QID2": ["2"],        # alternate legal name? -> No
    "QID9": ["67"],       # Country -> Greece
    "QID21": ["2"],       # another app w/ CM standard? -> No
    "QID23": ["3"],       # why -> developing new product/service
    "QID25": ["3"],       # primary use case -> Direct Advertiser
    "QID14": ["1", "2", "3"],  # Page mgmt + Page analytics + Profile mgmt
    "QID28": ["2"],       # customers based -> Europe
    "QID29": ["1"],       # clients -> 1-50
    "QID30": ["4"],       # % B2B -> 75%+
    "QID31": ["1"],       # customer category -> Small-Medium
    "QID32": ["2"],       # member profiles -> Brand managing execs
    "QID36": ["1"],       # privacy-compliance owner -> Yes
    "QID37": ["1"],       # delete-all-data capability -> Yes
    "QID38": ["2"],       # personal data for ads/sales -> No
    "QID47": ["2"],       # page data shared w/ other customers -> No
    "QID41": ["1", "2", "3", "6"],  # FB + IG + X + TikTok
}
# conditional TE/MC skipped: QID3,QID22 (alt=No), QID39,QID40 (ads=No),
# QID48 (sharing=No)

FILL_JS = r"""
((spec) => {
  const se = (window.Qualtrics && Qualtrics.SurveyEngine) || null;
  if (!se || !se.QuestionData) return JSON.stringify({fatal:'no SurveyEngine'});
  const done=[], miss=[];
  // MC via engine API
  for (const [qid, ids] of Object.entries(spec.choice||{})) {
    let q; try { q = se.QuestionData.getInstance(qid); } catch(e){ miss.push(qid+':noq'); continue; }
    if (!q) { miss.push(qid+':null'); continue; }
    let ok = true;
    for (const id of ids) {
      try { q.setChoiceValue(String(id), true); } catch(e){ ok=false; }
    }
    // verify at least one holds
    let held = ids.some(id => { try { return q.getChoiceValue(String(id)); } catch(e){ return false; } });
    (ok && held) ? done.push(qid) : miss.push(qid);
  }
  // TE via DOM native setter + events (harvested at nav time)
  for (const [qid, val] of Object.entries(spec.text||{})) {
    if (val == null) continue;
    const sec = document.getElementById('question-' + qid)
             || document.querySelector('[id^="question-' + qid + '"]');
    const t = sec && sec.querySelector('input[type=text],input[type=email],input:not([type]),textarea');
    if (!t) { miss.push(qid + ':notfound'); continue; }
    const p = t.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const set = Object.getOwnPropertyDescriptor(p, 'value').set;
    t.focus(); set.call(t, val);
    t.dispatchEvent(new Event('input', {bubbles:true}));
    t.dispatchEvent(new Event('change', {bubbles:true}));
    t.dispatchEvent(new Event('blur', {bubbles:true}));
    done.push(qid);
  }
  // visible question signature for progress detection
  const sig = [...new Set([...document.querySelectorAll('[id^="question-QID"]')]
    .filter(e => e.offsetParent && !(e.id||'').includes('display'))
    .map(e => e.id.replace('question-','')))].sort();
  return JSON.stringify({done, miss, sig, fatal:null});
})
"""

ERRORS_JS = r"""
(() => {
  const errs = [...document.querySelectorAll('.ValidationError,[class*=error-message],.QuestionError,[class*=ErrorText]')]
    .filter(e => e.offsetParent && (e.innerText||'').trim())
    .map(e => {
      let sec = e.closest('[id^="question-"]');
      return {qid: sec ? sec.id.replace('question-','') : null,
              msg: e.innerText.replace(/\s+/g,' ').trim().slice(0,100)};
    }).slice(0,10);
  const qids = [...new Set(errs.map(e=>e.qid).filter(Boolean))];
  return JSON.stringify({errs, qids});
})()
"""

STATE_JS = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  const qs = [...document.querySelectorAll('[id^="question-QID"]')]
    .filter(e => e.offsetParent && !(e.id||'').includes('display')).length;
  return JSON.stringify({
    done: /thank you|response has been recorded|submitted|we will be in touch/i.test(text),
    questions: qs, url: location.href,
    snippet: text.slice(0, 200)});
})()
"""


def env_map(path):
    out = {}
    try:
        for line in open(path, errors="replace"):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip("\"'")
    except OSError:
        pass
    return out


def _cdp_base():
    for base in CDP_CANDIDATES:
        try:
            tabs = json.loads(urllib.request.urlopen(
                base + "/json", timeout=5).read())
            if tabs:
                return base
        except Exception:
            continue
    return None


def _open_tab(base, url):
    req = urllib.request.Request(
        base + "/json/new?" + urllib.parse.quote(url, safe=""),
        method="PUT")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def _close_tab(base, tab_id):
    try:
        urllib.request.urlopen(base + "/json/close/" + tab_id, timeout=5)
    except Exception:
        pass


class CDP:
    """CDP session with a background reader — Qualtrics floods events and
    the relay kills sockets whose buffers fill. The page survives a dead
    socket, so eval() transparently re-attaches to the same tab."""

    def __init__(self, ws_url, base=None, tab_id=None):
        import threading
        self._base = base
        self._tab_id = tab_id
        self._connect(ws_url)

    def _connect(self, ws_url):
        import threading
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=30,
                                              suppress_origin=True)
        self._responses = {}
        self._cv = __import__("threading").Condition()
        self._dead = None
        self._mid = 0
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reconnect(self):
        if not (self._base and self._tab_id):
            return False
        try:
            tabs = json.loads(urllib.request.urlopen(
                self._base + "/json/list", timeout=5).read())
            tab = next((t for t in tabs if t.get("id") == self._tab_id),
                       None)
            if not tab:
                return False
            try:
                self.ws.close()
            except Exception:
                pass
            self._connect(tab["webSocketDebuggerUrl"])
            return True
        except Exception:
            return False

    def _reader(self):
        try:
            while True:
                msg = json.loads(self.ws.recv())
                mid = msg.get("id")
                if mid is not None:
                    with self._cv:
                        self._responses[mid] = msg.get("result", {})
                        self._cv.notify_all()
        except Exception as e:
            self._dead = e
            with self._cv:
                self._cv.notify_all()

    def cmd(self, method, params=None, timeout=60):
        self._mid += 1
        mid = self._mid
        try:
            self.ws.send(json.dumps({"id": mid, "method": method,
                                     "params": params or {}}))
        except Exception as e:
            return {"_error": str(e)}
        deadline = time.time() + timeout
        import threading
        with self._cv:
            while mid not in self._responses:
                if self._dead:
                    return {"_error": f"ws closed: {self._dead}"}
                remaining = deadline - time.time()
                if remaining <= 0:
                    return {"_error": "timeout"}
                self._cv.wait(remaining)
            return self._responses.pop(mid)

    def eval(self, expr):
        for attempt in range(2):
            r = self.cmd("Runtime.evaluate", {
                "expression": expr, "returnByValue": True,
                "awaitPromise": True})
            v = r.get("result", {}).get("value")
            if v is not None or "_error" not in r:
                return v
            if attempt == 0 and self._reconnect():
                continue
            return v
        return None

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# ---------- public recording URL ----------
#
# Order: CM_RECORDING_URL env -> SocialAuto media library (unauthenticated
# /api/v1/media/view serves storage_path) -> Nextcloud public share.

def _sa_token():
    cfg = env_map(ENV_FILE)
    email = cfg.get("SOCIAL_ADMIN_EMAIL")
    pw = cfg.get("SOCIAL_ADMIN_PASSWORD")
    if not (email and pw):
        return None
    api = os.environ.get("SOCIAL_API_URL", "http://127.0.0.1:8083")
    req = urllib.request.Request(
        api + "/api/v1/auth/login",
        data=urllib.parse.urlencode(
            {"username": email, "password": pw}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read()
                      )["access_token"], api


def _multipart(fields, file_field, path, filename):
    boundary = "----cm" + os.urandom(8).hex()
    body = bytearray()
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{k}"\r\n\r\n{v}\r\n').encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; "
             f'name="{file_field}"; filename="{filename}"\r\n'
             "Content-Type: video/mp4\r\n\r\n").encode()
    body += open(path, "rb").read()
    body += f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def media_url(mp4):
    """Upload to the SocialAuto media library; return the public view URL."""
    try:
        got = _sa_token()
        if not got:
            return None
        token, api = got
        hdr = {"Authorization": f"Bearer {token}"}
        teams = json.loads(urllib.request.urlopen(urllib.request.Request(
            api + "/api/v1/teams", headers=hdr), timeout=30).read())
        team_id = teams[0]["id"]
        body, ctype = _multipart(
            {"alt_text": "CM survey screen recording",
             "tags": "linkedin,cm-survey,screen-recording"},
            "file", mp4, os.path.basename(mp4))
        req = urllib.request.Request(
            f"{api}/api/v1/media/upload?team_id={team_id}",
            data=body, method="POST",
            headers={**hdr, "Content-Type": ctype})
        asset = json.loads(urllib.request.urlopen(req, timeout=300).read())
        if asset.get("public_url"):
            return asset["public_url"]
        sp = asset.get("storage_path")
        if not sp:
            return None
        return ("https://social.cloudless.gr/api/v1/media/view?path="
                + urllib.parse.quote(sp, safe=""))
    except Exception as e:
        print(f"media-library upload failed: {e}")
        return None


def nc_creds():
    cfg = env_map(ENV_FILE)
    base = cfg.get("NEXTCLOUD_BASE_URL", "").rstrip("/")
    user = cfg.get("NEXTCLOUD_USERNAME") or cfg.get("NEXTCLOUD_USER", "")
    pw = cfg.get("NEXTCLOUD_PASSWORD", "")
    return base, user, pw


def _nc_req(method, url, user, pw, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Authorization",
                   "Basic " + __import__("base64").b64encode(
                       f"{user}:{pw}".encode()).decode())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=timeout)


def recording_url(mp4):
    """Public URL for the mp4 — CM_RECORDING_URL wins, else upload to
    Nextcloud + create a public share link."""
    if os.environ.get("CM_RECORDING_URL"):
        return os.environ["CM_RECORDING_URL"]
    u = media_url(mp4)
    if u:
        return u
    base, user, pw = nc_creds()
    if not (base and user and pw):
        return None
    name = "cm-survey-" + os.path.basename(mp4)
    dav = (f"{base}/remote.php/dav/files/{urllib.parse.quote(user)}"
           "/cm-survey")
    try:
        _nc_req("MKCOL", dav, user, pw, timeout=20)
    except Exception:
        pass
    with open(mp4, "rb") as f:
        blob = f.read()
    _nc_req("PUT", f"{dav}/{urllib.parse.quote(name)}", user, pw,
            data=blob,
            headers={"Content-Type": "application/octet-stream"},
            timeout=300)
    ocs = (f"{base}/ocs/v2.php/apps/files_sharing/api/v1/shares"
           f"?path=/cm-survey/{urllib.parse.quote(name)}"
           "&shareType=3&publicUpload=false")
    xml = _nc_req("POST", ocs, user, pw,
                  headers={"OCS-APIRequest": "true"}, timeout=30).read()
    m = re.search(rb"<url>(.*?)</url>", xml)
    if not m:
        return None
    return m.group(1).decode().rstrip("/") + "/download"


# ---------- Q_DL form-url discovery ----------

def find_form_url(base):
    """Scrape the CM product's 'Access request form' link (Q_DL-bound)
    from the app's products page. Best-effort; returns None on failure."""
    try:
        tab = _open_tab(base, APP_PRODUCTS)
        time.sleep(9)
        c = CDP(tab["webSocketDebuggerUrl"], base=base, tab_id=tab.get("id",""))
        try:
            links = c.eval(r"""
JSON.stringify([...document.querySelectorAll('a[href]')]
  .map(a => a.href)
  .filter(h => /qualtrics|jfe\\/form|access.request/i.test(h)))
""")
            urls = json.loads(links or "[]")
            cm = [u for u in urls if "qualtrics" in u]
            if cm:
                # prefer the Community Management SV if several forms exist
                pick = cm[0]
                return pick
        finally:
            c.close()
            _close_tab(base, tab.get("id", ""))
    except Exception:
        pass
    return None


# ---------- main ----------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--form-url" in sys.argv:
        i = sys.argv.index("--form-url")
        args.remove(sys.argv[i + 1])
        form_url = sys.argv[i + 1]
    else:
        form_url = os.environ.get("CM_FORM_URL", DEFAULT_FORM)
    dry = "--dry-run" in sys.argv or "--no-submit" in sys.argv
    if not args:
        print(__doc__)
        return 2
    mp4 = args[0]
    if not os.path.exists(mp4):
        print(f"mp4 not found: {mp4}")
        return 2

    rec = recording_url(mp4)
    if not rec:
        print("no recording URL — set CM_RECORDING_URL or fix Nextcloud creds")
        return 2
    print(f"recording -> {rec[:90]}")

    text = dict(TEXT)
    text["QID43"] = rec
    spec = {"text": text, "choice": CHOICE}

    base = _cdp_base()
    if not base:
        print("no CDP endpoint reachable")
        return 2

    # Q_DL binding: prefer the app-bound link scraped from the products page
    bound = find_form_url(base)
    if bound and "SV_3Q1SbLSZoImyw6O" in bound:
        form_url = bound
        print("using app-bound form URL (Q_DL)")
    elif bound:
        print(f"note: products page links to {bound[:80]}… — using it "
              f"only if it matches the CM survey")
        if "SV_3Q1SbLSZoImyw6O" not in bound:
            print("  -> not the CM survey; keeping default URL")
    else:
        print("WARN: no app-bound URL found — submitting may orphan. "
              "Set CM_FORM_URL to the Q_DL link if known.")

    print(f"cdp {base} — opening form")
    tab = _open_tab(base, form_url)
    tab_id = tab.get("id", "")
    time.sleep(10)
    c = CDP(tab["webSocketDebuggerUrl"], base=base, tab_id=tab.get("id",""))
    try:
        state = json.loads(c.eval(STATE_JS) or "{}")
        if state.get("done"):
            print("form already submitted (Thank-you)")
            return 0
        if not state.get("questions"):
            # dedupe guard: intro-only render means this session already
            # responded — clear qualtrics origin storage and reload once
            print("0 questions rendered — clearing origin storage, reloading")
            c.cmd("Storage.clearDataForOrigin", {
                "origin": "https://linkedincustomerops.qualtrics.com",
                "storageTypes": "all"})
            _close_tab(base, tab_id)
            tab = _open_tab(base, form_url)
            tab_id = tab.get("id", "")
            time.sleep(10)
            c = CDP(tab["webSocketDebuggerUrl"], base=base, tab_id=tab.get("id",""))
            state = json.loads(c.eval(STATE_JS) or "{}")

        for rnd in range(MAX_ROUNDS):
            if state.get("done"):
                print("SUBMITTED — confirmation page reached")
                return 0
            info_raw = c.eval(f"({FILL_JS})({json.dumps(spec)})")
            info = json.loads(info_raw) if info_raw else {}
            if info.get("fatal"):
                print(f"engine error: {info['fatal']} — DOM fallback needed")
                return 4
            print(f"round {rnd}: filled={len(info.get('done', []))} "
                  f"miss={info.get('miss')}")
            if dry:
                print("dry-run: filled, not submitting")
                return 0
            # click the REAL next button only
            clicked = c.eval(r"""
(() => {
  const b = document.getElementById('next-button')
         || document.querySelector('.navigation-button');
  if (!b || !b.offsetParent) return 'none';
  b.click();
  return (b.textContent || b.value || '').trim().slice(0, 40);
})()""")
            print(f"  next -> {clicked!r}")
            if clicked == "none":
                time.sleep(4)
                st = json.loads(c.eval(STATE_JS) or "{}")
                if st.get("done"):
                    print("confirmed after idle wait")
                    return 0
                print("no next control and no confirmation — stopping")
                return 1
            time.sleep(6)
            state = json.loads(c.eval(STATE_JS) or "{}")
            if state.get("done"):
                print("SUBMITTED — confirmation page reached")
                return 0
            errs = json.loads(c.eval(ERRORS_JS) or "{}")
            eq = errs.get("qids") or []
            if eq:
                print(f"  validation flagged: {eq} "
                      f"({[e['msg'] for e in errs.get('errs', [])][:3]})")
                # next round refills everything; conditional questions
                # that just appeared get picked up if mapped
            else:
                # page may have advanced — check question signature moved
                qs_now = state.get("questions")
                print(f"  questions now: {qs_now}")
        print("max rounds reached without confirmation")
        return 1
    finally:
        try:
            c.close()
        except Exception:
            pass
        _close_tab(base, tab_id)


if __name__ == "__main__":
    sys.exit(main())
