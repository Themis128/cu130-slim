#!/usr/bin/env python3
"""Gmail read access via OAuth (loopback consent) — stdlib only.

  python3 gmail.py login                          # mint GMAIL_REFRESH_TOKEN (opens browser)
  python3 gmail.py search "<gmail query>"         # e.g. 'newer_than:2d synigoros OR ombudsman'
Saves refresh token to GMAIL_REFRESH_TOKEN in .env; uses Gmail REST list/get.
"""
import json, os, re, sys, urllib.parse, urllib.request, threading, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ENV = REPO / ".env"
def _find_secret() -> Path:
    """Locate the OAuth client-secret — scans candidate paths so a renamed
    download or a repo copy works without code edits."""
    cands = list(Path("/mnt/c/Users/tbaltzakis/Downloads").glob("client_secret*.json"))
    cands += list(Path("/home/tbaltzakis").glob("**/client_secret*.json"))
    cands += list(Path.home().glob(".config/gmail/client_secret*.json"))
    if not cands:
        raise SystemExit(
            "client_secret JSON not found — drop it in Downloads or "
            "~/.config/gmail/ (Google Cloud console → OAuth client → download)")
    return sorted(cands, key=lambda p: p.stat().st_mtime, reverse=True)[0]

SECRET_JSON = _find_secret()

def _load_secret():
    d = json.loads(SECRET_JSON.read_text())
    return d.get("installed") or d["web"]

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
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = q.get("code", [None])[0]
            self.send_response(200); self.end_headers()
            self.wfile.write("<h3>Gmail access granted — you can close this tab.</h3>".encode())
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cs["client_id"],
        "redirect_uri": f"http://127.0.0.1:{port}",
        "response_type": "code", "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline", "prompt": "consent"})
    print(f"opening consent URL — approve in the browser that opens\n{url}")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    srv.handle_request()
    tok = _post("https://oauth2.googleapis.com/token", {
        "code": code_holder["code"], "client_id": cs["client_id"],
        "client_secret": cs["client_secret"],
        "redirect_uri": f"http://127.0.0.1:{port}", "grant_type": "authorization_code"})
    _save_env("GMAIL_REFRESH_TOKEN", tok["refresh_token"])
    print("saved GMAIL_REFRESH_TOKEN to .env")

def _access():
    cs = _load_secret()
    return _post("https://oauth2.googleapis.com/token", {
        "client_id": cs["client_id"], "client_secret": cs["client_secret"],
        "refresh_token": _env("GMAIL_REFRESH_TOKEN"),
        "grant_type": "refresh_token"})["access_token"]

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

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    (login if sys.argv[1] == "login" else lambda: search(" ".join(sys.argv[2:])))()
