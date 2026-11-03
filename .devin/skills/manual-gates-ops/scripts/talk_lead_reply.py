#!/usr/bin/env python3
"""talk_lead_reply.py — reply to a visitor chat lead in Nextcloud Talk.

Visitor chats land as guest rooms in Talk — no contact info, so the lead
only exists inside that room. This finds the room (recent rooms whose
last messages match a needle, e.g. 'free audit') and posts a reply in
the agent's voice while the visitor may still be watching.

    talk_lead_reply.py --needle "free audit" --reply "Thanks! ..."

    --rooms              list recent rooms and exit (discovery)
    --room-token TOKEN   reply into a specific room
    --reply "..."        message text (default: audit CTA)

Talk OCS API uses basic auth — NEXTCLOUD_USER/NEXTCLOUD_PASSWORD (or the
same creds nextcloud_talk.py resolves) must be in env.
"""
import argparse
import os
import sys

import httpx

OCS = "/ocs/v2.php/apps/spreed/api/v1"

DEFAULT_REPLY = (
    "Thanks for reaching out! Book your free 30-minute audit here: "
    "https://cloudless.gr/contact — or reply here with your email and "
    "we'll schedule it for you."
)


def _creds() -> tuple[str, str, str]:
    base = (os.environ.get("NEXTCLOUD_BASE_URL")
            or os.environ.get("NEXTCLOUD_URL") or "").rstrip("/")
    user = os.environ.get("NEXTCLOUD_USER") or os.environ.get("NEXTCLOUD_USERNAME", "")
    pw = os.environ.get("NEXTCLOUD_PASSWORD") or os.environ.get("NEXTCLOUD_APP_PASSWORD", "")
    if not (base and user and pw):
        raise SystemExit("need NEXTCLOUD_BASE_URL + NEXTCLOUD_USER + NEXTCLOUD_PASSWORD")
    return base, user, pw


def rooms(c: httpx.Client, base: str) -> list[dict]:
    r = c.get(f"{base}{OCS}/room", headers={"OCS-APIRequest": "true"}, timeout=20)
    r.raise_for_status()
    return (r.json().get("ocs", {}).get("data")) or []


def last_messages(c: httpx.Client, base: str, token: str, n: int = 5) -> list[dict]:
    r = c.get(
        f"{base}{OCS}/chat/{token}",
        params={"lookIntoFuture": 0, "limit": n},
        headers={"OCS-APIRequest": "true"},
        timeout=20,
    )
    if r.status_code != 200:
        return []
    return (r.json().get("ocs", {}).get("data")) or []


def post(c: httpx.Client, base: str, token: str, text: str) -> int:
    r = c.post(
        f"{base}{OCS}/chat/{token}",
        json={"message": text},
        headers={"OCS-APIRequest": "true"},
        timeout=20,
    )
    return r.status_code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--needle", default="audit")
    ap.add_argument("--reply", default=DEFAULT_REPLY)
    ap.add_argument("--room-token", default="")
    ap.add_argument("--rooms", action="store_true")
    a = ap.parse_args()

    base, user, pw = _creds()
    with httpx.Client(auth=(user, pw)) as c:
        all_rooms = rooms(c, base)
        if a.rooms:
            for r in all_rooms:
                print(f"{r['token']}  {r.get('displayName','')}  type={r.get('type')}")
            return 0
        targets = [a.room_token] if a.room_token else []
        if not targets:
            for r in all_rooms:
                msgs = last_messages(c, base, r["token"])
                hay = " ".join(str(m.get("message", "")) for m in msgs).lower()
                if a.needle.lower() in hay:
                    targets.append(r["token"])
                    print(f"match: {r['token']} {r.get('displayName','')}")
        if not targets:
            print("no room matched the needle")
            return 1
        for t in targets:
            print(t, "→", post(c, base, t, a.reply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
