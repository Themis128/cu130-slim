#!/usr/bin/env python3
"""Gmail read access via OAuth (loopback consent) — stdlib only.

  python3 gmail.py login                          # mint GMAIL_REFRESH_TOKEN (opens browser)
  python3 gmail.py search "<gmail query>"         # e.g. 'newer_than:2d synigoros OR ombudsman'
Saves refresh token to GMAIL_REFRESH_TOKEN in .env; uses Gmail REST list/get.
"""
import json
import re
import sys
import urllib.parse
import urllib.request
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ENV = REPO / ".env"
SECRET_JSON = Path("/mnt/c/Users/tbaltzakis/Downloads/client_secret_2_252848180858-rlv4jkvs95a4ct9gs2bf0kggm8tc4rq6.apps.googleusercontent.com.json")

def _load_secret():
    if SECRET_JSON.exists():
        d = json.loads(SECRET_JSON.read_text())
        return d.get("installed") or d["web"]
    cid, csec = _env("GOOGLE_PHOTOS_CLIENT_ID"), _env("GOOGLE_PHOTOS_CLIENT_SECRET")
    if cid and csec:
        return {"client_id": cid, "client_secret": csec,
                "redirect_uris": ["http://localhost:50000/auth/callback"]}
    raise SystemExit(f"no client secret at {SECRET_JSON} and no GOOGLE_PHOTOS_* in env")

def _env(key):
    for line in ENV.read_text(errors="replace").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return None

def _save_env(key, val):
    s = ENV.read_text(errors="replace")
    if re.search(rf"^{key}=", s, re.M):
        s = re.sub(rf"^{key}=.*", f"{key}={val}", s, flags=re.M)
    else:
        s += f"\n{key}={val}\n"
    ENV.write_text(s)

def _post(url, data):
    r = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
    return json.loads(urllib.request.urlopen(r, timeout=30).read())

def login():
    cs = _load_secret()
    code_holder = {}
    # The OAuth client is a web-app type — only pre-registered redirect URIs
    # work. localhost:50000/auth/callback is on the client's allow-list.
    redirect = "http://localhost:50000/auth/callback"
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/auth/callback"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                code_holder["code"] = q.get("code", [None])[0]
                self.send_response(200); self.end_headers()
                self.wfile.write("<h3>Gmail access granted — you can close this tab.</h3>".encode())
            else:
                self.send_response(404); self.end_headers()
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 50000), H)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cs["client_id"],
        "redirect_uri": redirect,
        "response_type": "code", "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline", "prompt": "consent"})
    print(f"opening consent URL — approve in the browser that opens\n{url}")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    srv.handle_request()
    tok = _post("https://oauth2.googleapis.com/token", {
        "code": code_holder["code"], "client_id": cs["client_id"],
        "client_secret": cs["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    _save_env("GMAIL_REFRESH_TOKEN", tok["refresh_token"])
    print("saved GMAIL_REFRESH_TOKEN to .env")

def _access(env_key="GMAIL_REFRESH_TOKEN"):
    cs = _load_secret()
    return _post("https://oauth2.googleapis.com/token", {
        "client_id": cs["client_id"], "client_secret": cs["client_secret"],
        "refresh_token": _env(env_key),
        "grant_type": "refresh_token"})["access_token"]

def _consent(scope: str, env_key: str) -> None:
    """Shared loopback consent — mints a refresh token for `scope`."""
    cs = _load_secret()
    code_holder = {}
    redirect = "http://localhost:50000/auth/callback"
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/auth/callback"):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                code_holder["code"] = q.get("code", [None])[0]
                self.send_response(200); self.end_headers()
                self.wfile.write("<h3>Access granted — you can close this tab.</h3>".encode())
            else:
                self.send_response(404); self.end_headers()
        def log_message(self, *a): pass
    try:
        srv = HTTPServer(("127.0.0.1", 50000), H)
    except OSError:
        print("port 50000 busy — is another consent flow running?"); raise SystemExit(1)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cs["client_id"], "redirect_uri": redirect,
        "response_type": "code", "scope": scope,
        "access_type": "offline", "prompt": "consent"})
    print(f"opening consent URL — approve in the browser that opens\n{url}")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    srv.handle_request()
    tok = _post("https://oauth2.googleapis.com/token", {
        "code": code_holder["code"], "client_id": cs["client_id"],
        "client_secret": cs["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    _save_env(env_key, tok["refresh_token"])
    print(f"saved {env_key} to .env")

def login(account: str = ""):
    key = "GMAIL_REFRESH_TOKEN" + (f"_{account.upper().replace('.', '_').replace('@', '_')}" if account else "")
    _consent("https://www.googleapis.com/auth/gmail.readonly", key)

def search_account(query: str, account: str):
    """Search one named account's inbox (token minted via `login <account>`)."""
    key = f"GMAIL_REFRESH_TOKEN_{account.upper().replace('.', '_').replace('@', '_')}"
    if not _env(key):
        raise SystemExit(f"no token for {account} — run: gmail.py login {account}")
    a = _access(key)
    q = urllib.parse.urlencode({"q": query, "maxResults": 15})
    r = urllib.request.Request(f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{q}",
                               headers={"Authorization": f"Bearer {a}"})
    for m in json.loads(urllib.request.urlopen(r, timeout=30).read()).get("messages", []):
        r = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date",
            headers={"Authorization": f"Bearer {a}"})
        h = {x["name"]: x["value"] for x in json.loads(urllib.request.urlopen(r, timeout=30).read())["payload"]["headers"]}
        print(f"{h.get('Date','')} | {h.get('From','')[:50]} | {h.get('Subject','')[:80]}")

def login_admin():
    """Consent for service.management — enables the Gmail API on the project."""
    _consent("https://www.googleapis.com/auth/service.management", "GCP_REFRESH_TOKEN")

def enable_gmail():
    a = _access("GCP_REFRESH_TOKEN")
    cs = _load_secret()
    project = cs.get("project_id") or "252848180858"
    r = urllib.request.Request(
        f"https://serviceusage.googleapis.com/v1/projects/{project}/services/gmail.googleapis.com:enable",
        data=b"{}", method="POST",
        headers={"Authorization": f"Bearer {a}", "Content-Type": "application/json"})
    try:
        out = json.loads(urllib.request.urlopen(r, timeout=30).read())
        print("enabled:", out.get("name") or out)
    except Exception as e:
        print("err:", getattr(e, "read", lambda: e)() if hasattr(e, "read") else e)

def search(query):
    a = _access()
    q = urllib.parse.urlencode({"q": query, "maxResults": 15})
    r = urllib.request.Request(f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{q}",
                               headers={"Authorization": f"Bearer {a}"})
    msgs = json.loads(urllib.request.urlopen(r, timeout=30).read()).get("messages", [])
    for m in msgs:
        r = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date",
            headers={"Authorization": f"Bearer {a}"})
        h = {x["name"]: x["value"] for x in json.loads(urllib.request.urlopen(r, timeout=30).read())["payload"]["headers"]}
        print(f"{h.get('Date','')} | {h.get('From','')[:50]} | {h.get('Subject','')[:80]}")

def links(query, account=""):
    """Print one http(s) URL per line from bodies of messages matching query."""
    key = (f"GMAIL_REFRESH_TOKEN_{account.upper().replace('.', '_').replace('@', '_')}"
           if account else "GMAIL_REFRESH_TOKEN")
    a = _access(key)
    q = urllib.parse.urlencode({"q": query, "maxResults": 5})
    r = urllib.request.Request(f"https://gmail.googleapis.com/gmail/v1/users/me/messages?{q}",
                               headers={"Authorization": f"Bearer {a}"})
    import base64
    for m in json.loads(urllib.request.urlopen(r, timeout=30).read()).get("messages", []):
        r = urllib.request.Request(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}?format=full",
            headers={"Authorization": f"Bearer {a}"})
        msg = json.loads(urllib.request.urlopen(r, timeout=30).read())
        def bodies(pl):
            if pl.get("mimeType","").startswith("text/") and pl.get("body",{}).get("data"):
                yield base64.urlsafe_b64decode(pl["body"]["data"]).decode("utf-8","replace")
            for p_ in pl.get("parts",[]):
                yield from bodies(p_)
        seen=set()
        for b in bodies(msg["payload"]):
            for u in re.findall(r"https?://\S+", b):
                u=u.rstrip(".,);")
                if u not in seen:
                    seen.add(u); print(u)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "login" and len(sys.argv) > 2:
        login(sys.argv[2]); sys.exit(0)
    if cmd == "search-account" and len(sys.argv) > 3:
        search_account(sys.argv[3], sys.argv[2]); sys.exit(0)
    if cmd == "links":
        links(" ".join(sys.argv[2:])); sys.exit(0)
    if cmd == "links-account" and len(sys.argv) > 3:
        links(sys.argv[3], sys.argv[2]); sys.exit(0)
    {"login": login, "login-admin": login_admin, "enable": enable_gmail}.get(
        cmd, lambda: search(" ".join(sys.argv[1:])))()
