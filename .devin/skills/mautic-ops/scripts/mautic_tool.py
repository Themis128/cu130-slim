#!/usr/bin/env python3
"""mautic_tool — Mautic ops CLI. Run inside social-api: python3 scripts/mautic_tool.py <cmd>"""
import asyncio, sys
sys.path.insert(0, "/app")
from app.services import mautic_client as m
from app.services.mautic_client import _cfg, _headers
import httpx

async def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    base, user, pw, host = _cfg()
    async with httpx.AsyncClient(timeout=15, auth=(user, pw)) as c:
        h = _headers()
        if cmd == "status":
            print(await m.funnel_state())
        elif cmd == "segments":
            r = await c.get(base + "/api/segments?limit=50", headers=h)
            for s in (r.json().get("lists") or {}).values():
                print(s["id"], s.get("name"), "members:", s.get("contactsCount") or s.get("leadCount"))
        elif cmd == "contacts":
            lim = sys.argv[2] if len(sys.argv) > 2 else "20"
            r = await c.get(base + f"/api/contacts?limit={lim}&minimal=true", headers=h)
            for k, v in (r.json().get("contacts") or {}).items():
                f = v.get("fields", {}).get("core", {})
                print(k, f.get("email", {}).get("value"), "|", (f.get("firstname", {}) or {}).get("value"))
        elif cmd == "create-segment":
            print(await m.create_segment(sys.argv[2], sys.argv[3]))
        elif cmd == "upsert":
            email, first, last, company = sys.argv[2:6]
            r = await m.upsert_contact(email, {"firstname": first, "lastname": last, "company": company})
            print((r.get("contact") or {}).get("id") or r)
        elif cmd == "add-to-segment":
            r = await c.get(base + "/api/contacts?search=email:" + sys.argv[3], headers=h)
            ids = list((r.json().get("contacts") or {}).keys())
            print(await m.add_to_segment(int(ids[0]), int(sys.argv[2])) if ids else "contact not found")
        elif cmd == "campaigns":
            r = await c.get(base + "/api/campaigns?limit=50", headers=h)
            for k, v in (r.json().get("campaigns") or {}).items():
                print(k, v.get("name"), "published:", v.get("isPublished"))
        elif cmd == "emails":
            r = await c.get(base + "/api/emails?limit=50", headers=h)
            for k, v in (r.json().get("emails") or {}).items():
                print(k, v.get("name"), "|", v.get("subject"), "sent:", v.get("sentCount"))
        elif cmd == "send":
            email_id, contact = sys.argv[2], sys.argv[3]
            r = await c.get(base + "/api/contacts?search=email:" + contact, headers=h)
            ids = list((r.json().get("contacts") or {}).keys())
            if ids:
                print((await c.post(base + f"/api/emails/{email_id}/contact/{ids[0]}/send", headers=h)).status_code)
            else: print("contact not found")
        else:
            print(__doc__)

asyncio.run(main())
