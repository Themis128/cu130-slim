#!/usr/bin/env python3
# ruff: noqa: E402
"""Instagram lane doctor — per-account publishability probe.

SocialAuto can hold MULTIPLE social_accounts rows for the same Instagram
profile with different credential products:

  login_type=business_login → Instagram Business Login token
      host graph.instagram.com, scopes instagram_business_* — publish lane
      independent of any Facebook Page. Refreshable without password via
      GET graph.instagram.com/refresh_access_token (beat: weekly).

  login_type=fb_onboarding  → Facebook Login / Page token
      host graph.facebook.com, scopes instagram_*+pages_* — publish needs
      Page Publishing Authorization (PPA): the Page↔IG link must be
      confirmed. When it is not, POST /{ig-user-id}/media fails Graph
      error (#10) "Requires instagram_content_publish permission" EVEN
      THOUGH the token already carries that permission. The error text
      lies — it is a linkage state, not a missing scope.

This tool reports, per row: token validity on the correct host, whether
the row can publish, WHY NOT, and which row is canonical for new posts
(content.py auto-select prefers login_type=business_login).

Run inside the social-api / worker container:

    docker exec social-api python3 /app/scripts/ig_lane_doctor.py <cmd>

Commands:
    report              per-row lane table (default)
    pending             pending IG post_targets grouped by lane
    repoint <acct>      move ALL pending IG targets to the given account
                        (id prefix, e.g. 38ddbd44); prints each move
    publish-probe <acct> create a throwaway media container on the account
                        (never published — container expires) to verify the
                        publish lane without shipping a post
Never prints tokens.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

for path in ("/app", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
    if path not in sys.path and os.path.isdir(os.path.join(path, "app")):
        sys.path.insert(0, path)

# Public, stable, tiny image used only for container probes. Unpublished
# containers expire server-side; nothing is ever posted.
_PROBE_IMAGE = "https://pub-9dff040689a94928938b78da104c1ec4.r2.dev/2026/11/21/e3061270721345a9.jpg"


async def _accounts() -> list:
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async with async_session_maker() as db:
        result = await db.execute(select(SocialAccount).where(SocialAccount.platform == "instagram"))
        return list(result.scalars().all())


def _token_for(account) -> str | None:
    from app.core.security import decrypt_token

    enc = account.access_token_enc
    if not enc:
        return None
    try:
        return decrypt_token(enc if isinstance(enc, bytes) else enc.encode())
    except Exception:
        return None


def _client_for(account):
    from app.services.instagram_api import InstagramAPIClient

    return InstagramAPIClient(
        access_token=_token_for(account) or "invalid",
        ig_user_id=account.account_id,
        use_business_login_api=(account.meta_data or {}).get("login_type") == "business_login",
    )


async def _fb_page_link_state(token: str, expected_ig_id: str | None) -> dict:
    """For fb_onboarding rows: which Page is this, and which IG does it link?

    A Page token resolves /me → the Page. connected_instagram_account then
    reveals the linked IG professional id. Three outcomes:
      linked-ok      → id matches the row's account_id (publish viable, PPA
                       may still be incomplete — see --probe-publish)
      linked-other   → Page links a DIFFERENT IG id (wrong object → #10)
      unlinked       → Page has no connected IG (PPA/link not done → #10)
    """
    import httpx

    base = "https://graph.facebook.com/v26.0"
    async with httpx.AsyncClient(timeout=20) as http:
        try:
            me = (await http.get(f"{base}/me", params={"access_token": token})).json()
        except Exception as e:  # noqa: BLE001
            return {"state": "probe-error", "detail": str(e)[:160]}
    if "error" in me:
        return {"state": "token-invalid", "detail": me["error"].get("message", "?")[:160]}
    page_id = me.get("id")
    page_name = me.get("name", "?")
    async with httpx.AsyncClient(timeout=20) as http:
        try:
            page = (
                await http.get(
                    f"{base}/{page_id}",
                    params={"fields": "connected_instagram_account", "access_token": token},
                )
            ).json()
        except Exception as e:  # noqa: BLE001
            return {"state": "probe-error", "detail": str(e)[:160]}
    cia = (page.get("connected_instagram_account") or {}).get("id")
    out = {"page_id": page_id, "page_name": page_name, "linked_ig": cia}
    if not cia:
        out["state"] = "unlinked"
    elif cia == expected_ig_id:
        out["state"] = "linked-ok"
    else:
        out["state"] = "linked-other"
    return out


async def _bl_state(client) -> dict:
    """For business_login rows: token validity + identity on graph.instagram.com."""
    try:
        me = await client.get_me()
    except Exception as e:  # noqa: BLE001
        detail = getattr(e, "safe_detail", str(e))
        return {"state": "token-invalid", "detail": str(detail)[:200]}
    return {
        "state": "token-valid",
        "ig_id": me.get("id") or me.get("user_id"),
        "username": me.get("username"),
        "followers": me.get("followers_count"),
    }


async def cmd_report() -> None:
    rows = await _accounts()
    if not rows:
        print("no instagram accounts connected")
        return
    for a in rows:
        meta = a.meta_data or {}
        lt = meta.get("login_type", "?")
        print(f"\n=== {a.username or '(no username)'} [{str(a.id)[:8]}] ===")
        print(f"  status={a.status} login_type={lt} account_id={a.account_id}")
        token = _token_for(a)
        if not token:
            print("  token: MISSING/undecryptable — lane dead, needs reconnect")
            continue
        if lt == "business_login":
            st = await _bl_state(_client_for(a))
            print(f"  business-login probe: {st['state']}")
            for k in ("username", "ig_id", "followers", "detail"):
                if st.get(k) is not None:
                    print(f"    {k}: {st[k]}")
            ok = st["state"] == "token-valid"
            print(f"  publish lane: {'OK' if ok else 'BROKEN — re-auth via instagram2 authorize'}")
        else:
            st = await _fb_page_link_state(token, a.account_id)
            print(f"  fb/page probe: {st['state']}  page={st.get('page_name', '?')} ({st.get('page_id', '?')})")
            print(f"    linked_ig: {st.get('linked_ig')}  row_ig: {a.account_id}")
            if st["state"] == "token-invalid":
                print("  publish lane: BROKEN — token invalid, needs re-auth")
            elif st["state"] == "unlinked":
                print("  publish lane: BROKEN — Page has no connected IG (PPA/link not done → #10)")
            elif st["state"] == "linked-other":
                print("  publish lane: BROKEN — Page links a different IG object → #10")
            else:
                print("  publish lane: linked-ok — may still need PPA; use publish-probe to confirm")
    print("\ncanonical for new posts: content.py prefers login_type=business_login when active")


async def _pending_targets():
    from sqlalchemy import text

    from app.db.session import async_session_maker

    async with async_session_maker() as db:
        res = await db.execute(
            text(
                """SELECT pt.post_id, sa.id AS acct, sa.meta_data->>'login_type' AS lt,
                          pt.status
                   FROM post_targets pt
                   JOIN social_accounts sa ON sa.id = pt.social_account_id
                   WHERE sa.platform='instagram' AND pt.status='pending'"""
            )
        )
        return list(res)


async def cmd_pending() -> None:
    rows = await _pending_targets()
    if not rows:
        print("no pending instagram targets")
        return
    by: dict[str, int] = {}
    for r in rows:
        by[r.lt or "?"] = by.get(r.lt or "?", 0) + 1
    for lt, n in by.items():
        print(f"{n} pending target(s) on login_type={lt}")
    for r in rows:
        print(f"  {r.post_id} → {str(r.acct)[:8]} ({r.lt})")


async def cmd_repoint(acct_prefix: str) -> None:
    from sqlalchemy import text

    accounts = await _accounts()
    target = next((a for a in accounts if str(a.id).startswith(acct_prefix)), None)
    if not target:
        raise SystemExit(f"no instagram account matching {acct_prefix}")
    print(f"re-pointing pending IG targets → {target.username} [{str(target.id)[:8]}] ({(target.meta_data or {}).get('login_type')})")
    from app.db.session import async_session_maker

    async with async_session_maker() as db:
        res = await db.execute(
            text(
                """UPDATE post_targets pt SET social_account_id=:acct, updated_at=now()
                   FROM social_accounts sa
                   WHERE pt.status='pending' AND pt.social_account_id=sa.id
                     AND sa.platform='instagram'
                   RETURNING pt.post_id"""
            ),
            {"acct": target.id},
        )
        moved = [str(r[0]) for r in res]
        await db.commit()
    for pid in moved:
        print(f"  moved {pid}")
    print(f"{len(moved)} target(s) re-pointed")


async def cmd_publish_probe(acct_prefix: str) -> None:
    accounts = await _accounts()
    target = next((a for a in accounts if str(a.id).startswith(acct_prefix)), None)
    if not target:
        raise SystemExit(f"no instagram account matching {acct_prefix}")
    lt = (target.meta_data or {}).get("login_type")
    print(f"publish-probe on {target.username} [{str(target.id)[:8]}] ({lt})")
    print("creating a media container (NEVER published — it expires unpublished)")
    client = _client_for(target)
    try:
        cid = await client.create_image_container(image_url=_PROBE_IMAGE)
        print(f"  container created: {cid} — publish lane OK (container left unpublished)")
    except Exception as e:  # noqa: BLE001
        detail = getattr(e, "safe_detail", str(e))
        print(f"  container FAILED: {str(detail)[:300]}")
        if "content_publish" in str(detail) or "(#10)" in str(detail):
            print("  → error #10 shape: Page token cannot manage this IG object.")
            print("    For fb_onboarding rows this means PPA/link incomplete; use the")
            print("    business_login row or complete Page↔IG connection first.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["report", "pending", "repoint", "publish-probe"], nargs="?", default="report")
    ap.add_argument("acct", nargs="?", help="account id prefix (repoint / publish-probe)")
    args = ap.parse_args()

    coro = {
        "report": lambda: cmd_report(),
        "pending": lambda: cmd_pending(),
        "repoint": lambda: cmd_repoint(args.acct or ""),
        "publish-probe": lambda: cmd_publish_probe(args.acct or ""),
    }[args.cmd]
    asyncio.run(coro())
    return 0


if __name__ == "__main__":
    sys.exit(main())
