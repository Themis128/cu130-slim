"""IDIKA epidoma-paidiou watcher: one-shot check via CDP :9229 (chrome-debug-profile).
Compares the 'Aitiseis' table to state.json; posts changes to Telegram.
Schedule hourly via schtasks."""
import json, os, sys, time, urllib.request, urllib.parse

CDP = "http://localhost:9229"
KEYS = "C:/Users/tbaltzakis/AppData/Roaming/devin/secrets"
DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "state.json")
URL = "https://www.idika.gr/epidomapaidiou/2026/LandingPage"
CHAT_ID = "7017840434"
LOGIN_MARK = "/Account/Login"

BOT = open(KEYS + "/tg-bot-token").read().strip()

EXTRACT_JS = """(() => {
  const rows=[];
  document.querySelectorAll('table tr').forEach(tr=>{
    const cells=[...tr.querySelectorAll('th,td')].map(c=>c.innerText.trim().replace(/\\s+/g,' '));
    if(cells.length) rows.push(cells.join(' | '));
  });
  return JSON.stringify({url:location.href, title:document.title, rows:rows});
})()"""


def tg(text):
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{BOT}/sendMessage", data=data, timeout=15)
    except Exception as e:
        print("tg err", e)


def http_json(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = r"C:\Users\tbaltzakis\chrome-debug-profile"


def ensure_browser():
    try:
        http_json(CDP + "/json/version")
        return
    except Exception:
        pass
    import subprocess
    subprocess.Popen([CHROME, "--remote-debugging-port=9229",
                      f"--user-data-dir={PROFILE}", "--no-first-run",
                      "--no-default-browser-check", "about:blank"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        try:
            http_json(CDP + "/json/version")
            return
        except Exception:
            continue
    raise RuntimeError("chrome debug port never came up")


def find_page_target():
    for t in http_json(CDP + "/json/list"):
        if t.get("type") == "page" and "idika.gr" in t.get("url", ""):
            return t
    # create a new tab
    t = http_json(CDP + "/json/new?" + urllib.parse.quote(URL, safe=""), method="PUT")
    time.sleep(6)
    return t


def eval_js(ws_url, expr, reload_page=True):
    import websocket
    ws = websocket.create_connection(ws_url, timeout=40, suppress_origin=True)
    mid = 0

    def send(method, params=None):
        nonlocal mid
        mid += 1
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return mid

    if reload_page:
        send("Page.enable")
        send("Page.reload", {"ignoreCache": True})
        ws.settimeout(45)
        try:
            while True:
                m = json.loads(ws.recv())
                if m.get("method") == "Page.loadEventFired":
                    break
        except Exception as e:
            print("load wait err", e)
        time.sleep(2)
        ws.settimeout(40)
    rid = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    deadline = time.time() + 60
    last = None
    while time.time() < deadline:
        m = json.loads(ws.recv())
        if m.get("id") == rid:
            ws.close()
            res = m.get("result", {}).get("result", {})
            if res.get("subtype") == "error" or "exceptionDetails" in m.get("result", {}):
                print("eval error", json.dumps(m)[:500])
                return None
            if not res.get("value"):
                print("empty result:", json.dumps(m)[:500])
            return res.get("value")
        last = m
    ws.close()
    print("eval timeout; last:", json.dumps(last)[:300] if last else None)
    return None


def main():
    try:
        ensure_browser()
        target = find_page_target()
    except Exception as e:
        print("cdp err", e)
        st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
        if not st.get("down_alert"):
            tg("IDIKA watch: could not reach the debug browser — check the chrome-debug-profile window.")
            st["down_alert"] = True
            json.dump(st, open(STATE, "w", encoding="utf-8"))
        return
    raw = eval_js(target["webSocketDebuggerUrl"], EXTRACT_JS)
    if not raw:
        print("no eval result")
        return
    data = json.loads(raw)

    rows = data["rows"]
    sig = json.dumps(rows, ensure_ascii=False)
    authed = "Κωδικός" in sig  # applications table header present = logged in

    if not authed:
        import login
        ok = login.ensure_login(notify=tg)
        if ok:
            target = find_page_target()
            raw = eval_js(target["webSocketDebuggerUrl"], EXTRACT_JS)
            if raw:
                data = json.loads(raw)
                rows = data["rows"]
                sig = json.dumps(rows, ensure_ascii=False)
                json.dump({"sig": sig, "rows": rows, "ts": time.time()},
                          open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
                tg("IDIKA watch: auto re-login succeeded — session restored.")
                return
        st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
        if not st.get("login_alert"):
            tg("IDIKA watch: session expired and auto re-login FAILED — please log in manually in the debug Chrome window.")
            st["login_alert"] = True
            json.dump(st, open(STATE, "w", encoding="utf-8"))
        return

    st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    if st.get("sig") == sig:
        st.pop("login_alert", None)
        st.pop("down_alert", None)
        json.dump(st, open(STATE, "w", encoding="utf-8"))
        return

    changed = st.get("sig") is not None
    body = "IDIKA Epidoma Paidiou 2026 — CHANGE DETECTED\n" + "\n".join(rows[:20]) if changed \
        else "IDIKA Epidoma Paidiou 2026 — baseline:\n" + "\n".join(rows[:20])
    if changed:
        tg(body)
    st = {"sig": sig, "rows": rows, "ts": time.time()}
    st.pop("login_alert", None)
    st.pop("down_alert", None)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    print("changed" if changed else "baseline")


if __name__ == "__main__":
    main()
