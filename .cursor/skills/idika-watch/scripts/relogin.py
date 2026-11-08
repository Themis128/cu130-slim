"""Proactive daily re-login: forces a fresh GSIS+AMKA auth chain at a sane
hour so any 2FA/captcha challenge surfaces while the user is awake.
Run once daily via schtasks (09:00)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import login
from watch import tg

ok = login.ensure_login(notify=tg)
print("relogin:", "ok" if ok else "FAILED")
if ok:
    tg("IDIKA watch: morning session check OK — sessions refreshed.")
