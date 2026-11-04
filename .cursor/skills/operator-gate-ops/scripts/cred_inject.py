#!/usr/bin/env python3
"""cred_inject.py — universal safe credential intake.

Prompt via a local box (never echoed to the console), keep it in the
Windows Credential Manager vault, and optionally wire it into the SocialAuto
.env through env-manager — all without the value touching logs, .env on
disk by hand, or clipboard.

  python cred_inject.py set <vault_target> [--env <ENV_KEY>]   # prompt + store (+ write)
  python cred_inject.py get <vault_target> [--env <ENV_KEY>]   # reuse stored value
  python cred_inject.py delete <vault_target>

Examples
  cred_inject.py set cf-api --env CLOUDFLARE_API_TOKEN   # one paste -> vault + .env
  cred_inject.py set fb-app  --env FACEBOOK_CLIENT_SECRET
  cred_inject.py get fb-app                              # hands the stored value to tooling
"""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import urllib.request

REPO_ENV = r"C:\Users\tbaltzakis\cu130-slim\.env"
ENV_MGR = os.environ.get("ENV_MANAGER_URL", "http://localhost:8199")


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", wt.LPBYTE),
        ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR)]


adv = ctypes.windll.advapi32


def ask(prompt):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Add-Type -AssemblyName Microsoft.VisualBasic;"
         f"[Microsoft.VisualBasic.Interaction]::InputBox('{prompt}','Cloudless credential','')"],
        capture_output=True, text=True, timeout=300)
    return r.stdout.strip()


def cred_set(target, user, value):
    blob = value.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL(0, 1, target, None, wt.FILETIME(0, 0), len(blob),
                      ctypes.cast(buf, wt.LPBYTE), 2, 0, None, None, user)
    if not adv.CredWriteW(ctypes.byref(cred), 0):
        raise RuntimeError("CredWriteW failed")


def cred_get(target):
    p = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(target, 1, 0, ctypes.byref(p)):
        return None
    try:
        c = p.contents
        return (c.UserName,
                ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le"))
    finally:
        adv.CredFree(p)


def cred_delete(target):
    if not adv.CredDeleteW(target, 1, 0):
        raise RuntimeError("CredDeleteW failed (missing target?)")


def _env_val(key):
    try:
        with open(REPO_ENV, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def env_write(key, value):
    user, pw = _env_val("ENV_MANAGER_USER"), _env_val("ENV_MANAGER_PASS")
    req = urllib.request.Request(
        f"{ENV_MGR}/api/env/{key}", method="PUT",
        data=json.dumps({"value": value}).encode(),
        headers={"content-type": "application/json",
                 "Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()})
    return urllib.request.urlopen(req, timeout=15).status


def vault_name(short):
    return short if ":" in short else f"cloudless-gate:{short}"


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, target = sys.argv[1], vault_name(sys.argv[2])
    env_key = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--env=")), None)

    if cmd == "set":
        u = ask(f"Username/label for {sys.argv[2]} (or press OK empty)")
        v = ask(f"Value for {sys.argv[2]} — stored in Windows Credential Manager")
        if not v:
            sys.exit("no value supplied")
        cred_set(target, u or sys.argv[2], v)
        print(f"stored -> vault:{target}")
    elif cmd == "get":
        c = cred_get(target)
        if not c:
            sys.exit(f"nothing stored for {target}")
        u, v = c
        print(f"found (user={u})")
    elif cmd == "delete":
        cred_delete(target)
        print("deleted")
        return
    else:
        sys.exit(__doc__)

    if env_key:
        _, v = cred_get(target) or (None, None)
        if v:
            rc = env_write(env_key, v)
            print(f"{env_key} -> .env via env-manager (rc={rc})")


if __name__ == "__main__":
    main()
