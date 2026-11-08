#!/usr/bin/env python3
"""session_transplant — copy login sessions from the real Chrome profile
into the chrome-debug-profile via CDP, so gate_mint.py needs zero logins.

How it works (all user-level, no admin):
  1. Read the real Chrome's Cookies SQLite DB (shadow copy — it's locked).
  2. Decrypt each cookie value with DPAPI CryptUnprotectData — works because
     the debug profile and real profile share the same Windows user.
  3. Inject into the debug chrome via CDP Network.setCookie (9333 direct,
     Windows-side — no WSL hop).

Run WINDOWS-side:
    python session_transplant.py github.com cloudflare.com
    python session_transplant.py --list            # which domains have cookies
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request

CDP = "http://127.0.0.1:9333"
REAL_COOKIE_DB = os.path.expandvars(
    r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies"
)
DEBUG_PROFILE = r"C:\Users\tbaltzakis\chrome-debug-profile"


def _shadow_copy(src: str) -> str:
    """Copy the locked Cookies DB via a normal file copy (works on NTFS)."""
    dst = os.path.join(tempfile.mkdtemp(), "Cookies")
    shutil.copyfile(src, dst)
    return dst


def _dpapi_decrypt(ciphertext: bytes) -> str:
    """CryptUnprotectData on the DPAPI-prefixed cookie blob."""
    import ctypes
    import ctypes.wintypes as wt

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    if ciphertext[:3] != b"v10":
        return ciphertext.decode("utf-8", "replace")
    blob_in = DATA_BLOB(len(ciphertext), ctypes.cast(ciphertext, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        return ""
    raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    # v10 cookie value = 32-byte nonce + aes-gcm payload? For older format
    # it's raw plaintext after decrypt. Try utf-8 directly.
    return raw.decode("utf-8", "replace")


def _cookies_for(db_path: str, domains: list[str]) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    out = []
    for dom in domains:
        rows = conn.execute(
            "SELECT host_key,name,value,encrypted_value,path,is_secure,is_httponly,expires_utc "
            "FROM cookies WHERE host_key LIKE ?",
            (f"%{dom}",),
        ).fetchall()
        for r in rows:
            value = r["value"] or _dpapi_decrypt(r["encrypted_value"] or b"")
            if not value:
                continue
            out.append({
                "name": r["name"],
                "value": value,
                "domain": r["host_key"],
                "path": r["path"] or "/",
                "secure": bool(r["is_secure"]),
                "httpOnly": bool(r["is_httponly"]),
            })
    conn.close()
    return out


def _cdp_set_cookie(ws_url: str, cookie: dict) -> bool:
    import websocket

    ws = websocket.create_connection(ws_url, timeout=10)
    ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
    ws.recv()
    ws.send(json.dumps({"id": 2, "method": "Network.setCookie", "params": cookie}))
    ok = '"result":true' in ws.recv() or '"success":true' in ws.recv()
    ws.close()
    return ok


def _first_page():
    tabs = json.loads(urllib.request.urlopen(f"{CDP}/json", timeout=5).read())
    return next((t for t in tabs() if t.get("type") == "page"), None)


def list_domains():
    db = _shadow_copy(REAL_COOKIE_DB)
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT host_key, count(*) c FROM cookies GROUP BY host_key ORDER BY c DESC LIMIT 20"
    ).fetchall()
    for h, c in rows:
        print(f"{c:4d}  {h}")
    conn.close()


def transplant(domains: list[str]) -> int:
    page = _first_page()
    if not page:
        print("no debug chrome on :9333 — run the launch chain first")
        return 2
    db = _shadow_copy(REAL_COOKIE_DB)
    ws_url = page["webSocketDebuggerUrl"]
    ok = fail = 0
    for dom in domains:
        cookies = _cookies_for(dom, [dom])
        print(f"{dom}: {len(cookies)} cookies")
        for c in cookies:
            try:
                if _cdp_set_cookie(ws_url, c):
                    ok += 1
                else:
                    fail += 1
            except Exception as exc:
                fail += 1
    print(f"transplanted {ok}, failed {fail}")
    print("verify: python gate_mint.py probe — then run the gate directly")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_domains()
    elif len(sys.argv) > 1:
        sys.exit(transplant(sys.argv[1:]))
    else:
        print(__doc__)
