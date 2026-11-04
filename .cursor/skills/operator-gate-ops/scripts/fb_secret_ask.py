#!/usr/bin/env python3
"""fb_secret_ask.py — stage the Meta App Secret reveal and ASK the operator.

Drives the debug Chrome (:9222) to the Cloudless app settings, clicks
"Show" on App Secret, brings the Chrome window to front, and fires a
Windows toast + beep so the operator knows to type their FB password.
Once the dialog completes, hands off to fb_secret_reveal's capture path.

Usage: python fb_secret_ask.py          (stage + notify + capture)
       python fb_secret_ask.py --stage  (stage + notify only)
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_runner import _ensure_cdp, Page  # noqa: E402

APP_URL = "https://developers.facebook.com/apps/1936126137016578/settings/basic/"


def toast(msg):
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') | Out-Null;"
         "[reflection.assembly]::loadwithpartialname('System.Drawing') | Out-Null;"
         f"$n=New-Object System.Windows.Forms.NotifyIcon; $n.Icon=[System.Drawing.SystemIcons]::Information;"
         f"$n.Visible=$true; $n.ShowBalloonTip(12000,'Cloudless gate','{msg}','Info')"],
        capture_output=True, timeout=15)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "[console]::beep(1200,400); [console]::beep(1600,400)"],
                   capture_output=True, timeout=15)


def bring_front():
    # activate the chrome window so the password dialog is visible
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$p=Get-Process chrome -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowTitle} | Select-Object -First 1;"
         "if($p){$sig='[System.Runtime.InteropServices.DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(System.IntPtr h);';"
         "$t=Add-Type -MemberDefinition $sig -Name W -PassThru; $t::SetForegroundWindow($p.MainWindowHandle) | Out-Null}"],
        capture_output=True, timeout=15)


def stage():
    _ensure_cdp()
    p = Page()
    p.goto(APP_URL)
    time.sleep(6)
    t = p.poll("document.body.innerText", timeout=15) or ""
    if "App Secret" not in t:
        return "settings page didn't render App Secret row — is the debug profile logged into developers.facebook.com?"
    r = p.js("(()=>{const b=[...document.querySelectorAll('button,[role=button]')]"
             ".filter(x=>/^Show$/.test((x.innerText||'').trim()));"
             "if(b.length){b[b.length-1].click();return 'clicked'}return 'no-button'})()")
    if r == "no-button":
        return "no Show button — secret may already be revealed; run fb_secret_reveal.py directly"
    time.sleep(3)
    return "staged"


def main():
    print(stage())
    bring_front()
    toast("Type your Facebook password in the Meta dialog — the App Secret will be captured automatically")
    if "--stage" in sys.argv:
        return 0
    # hand off to the capture loop
    import fb_secret_reveal
    print("watching for dialog completion…")
    # reuse reveal's poll loop inline (don't re-navigate)
    deadline = time.time() + 600
    p = Page()
    while time.time() < deadline:
        if not fb_secret_reveal._dialog_open(p):
            sec = fb_secret_reveal._secret_value(p)
            if sec:
                rc = fb_secret_reveal._envmgr_put("FACEBOOK_CLIENT_SECRET", sec)
                print(f"secret captured + saved via env-manager (rc={rc})")
                return 0
        time.sleep(4)
    print("timeout — re-run after completing the password dialog")
    return 1


if __name__ == "__main__":
    sys.exit(main())
