#!/usr/bin/env python3
"""ig_edit.py — edit an Instagram post caption programmatically.

Same session chain as ig_delete.py (bridge profile login → WhatsApp 2FA
via ADB → fresh sessionid → instagrapi) but calls media_edit instead of
media_delete — updates the caption in place, preserving the post URL.

Run inside social-api (needs httpx + instagrapi). ADB from the WSL host.

    ig_edit.py <media_url_or_pk> "<new caption>"
"""
import sys
import time

from ig_delete import (
    fresh_sessionid,
    login_via_profile,
    start_ig_session,
)


def media_edit(pk_or_url: str, caption: str, sid: str) -> bool:
    from instagrapi import Client
    cl = Client()
    cl.delay_range = [1, 3]
    cl.login_by_sessionid(sid)
    if pk_or_url.startswith("http"):
        pk = cl.media_pk_from_url(pk_or_url)
    else:
        pk = int(pk_or_url)
    time.sleep(2)
    return bool(cl.media_edit(pk, caption))


def main() -> int:
    target, caption = sys.argv[1], sys.argv[2]
    print("starting IG session on bridge…")
    start_ig_session()
    print("logging in via remembered profile + WhatsApp 2FA…")
    login_via_profile()
    sid = fresh_sessionid()
    print("session live; editing caption on", target)
    print("edited:", media_edit(target, caption, sid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
