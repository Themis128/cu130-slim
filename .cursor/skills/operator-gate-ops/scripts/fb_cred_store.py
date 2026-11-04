#!/usr/bin/env python3
"""fb_cred_store.py — hold the FB password in Windows Credential Manager
so every later gate run is zero-touch.

  python fb_cred_store.py set            # prompts once, stores
  python fb_cred_store.py get            # returns "user|pass" for tooling
  python fb_cred_store.py delete

Credentials live in the Windows vault under target
'cloudless-gate:facebook.com' — never in files, env, or process args.
"""
import base64
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys

TARGET = "cloudless-gate:facebook.com"


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD), ("Type", wt.DWORD), ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR), ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD), ("CredentialBlob", wt.LPBYTE),
        ("Persist", wt.DWORD), ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR)]


advapi32 = ctypes.windll.advapi32


def _ask(prompt):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Add-Type -AssemblyName Microsoft.VisualBasic;"
         f"[Microsoft.VisualBasic.Interaction]::InputBox('{prompt}','Cloudless gate','')"],
        capture_output=True, text=True, timeout=300)
    return r.stdout.strip()


def cred_set(user, pw):
    blob = pw.encode("utf-16-le")
    blob_buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    ft = wt.FILETIME(0, 0)
    cred = CREDENTIAL(0, 1, TARGET, None, ft, len(blob),
                      ctypes.cast(blob_buf, wt.LPBYTE), 2, 0, None, None, user)
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise RuntimeError("CredWriteW failed")


def cred_get():
    p = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(TARGET, 1, 0, ctypes.byref(p)):
        return None
    try:
        c = p.contents
        pw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        return c.UserName, pw
    finally:
        advapi32.CredFree(p)


def cred_delete():
    if not advapi32.CredDeleteW(TARGET, 1, 0):
        raise RuntimeError("CredDeleteW failed (missing target?)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "get"
    if cmd == "set":
        u = _ask("Facebook email")
        p = _ask("Facebook password — stored in Windows Credential Manager")
        if not u or not p:
            sys.exit("no credentials supplied")
        cred_set(u, p)
        print("stored in Windows Credential Manager (cloudless-gate:facebook.com)")
    elif cmd == "get":
        c = cred_get()
        print("found" if c else "not found")
        if c:
            print("|".join([c[0], c[1]]))
    elif cmd == "delete":
        cred_delete()
        print("deleted")
    else:
        sys.exit("usage: set|get|delete")
