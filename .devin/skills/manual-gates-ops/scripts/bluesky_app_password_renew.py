#!/usr/bin/env python3
"""bluesky_app_password_renew.py — renew a dead Bluesky app password.

Probe the stored credential; on 401 mint a fresh app password via
``com.atproto.server.createAppPassword`` (PDS endpoint the bsky.app
Settings UI calls) and write it back through the backend's /connect
validation path. The only human input is the account's MAIN password —
app passwords cannot mint themselves.

    bluesky_app_password_renew.py --handle u.bsky.social
        [--main-password XXX] [--pds https://bsky.social] [--account-id UUID]

Run inside social-api (httpx + app session/db available).
"""
import argparse
import sys

import httpx


def probe(client: httpx.Client, pds: str, ident: str, pw: str) -> dict | None:
    r = client.post(
        f"{pds}/xrpc/com.atproto.server.createSession",
        json={"identifier": ident, "password": pw},
        timeout=30,
    )
    return r.json() if r.status_code == 200 else None


def mint_app_password(client: httpx.Client, pds: str, jwt: str, name: str) -> str:
    r = client.post(
        f"{pds}/xrpc/com.atproto.server.createAppPassword",
        json={"name": name},
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"createAppPassword {r.status_code}: {r.text[:200]}")
    return r.json()["password"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", required=True)
    ap.add_argument("--main-password", default="")
    ap.add_argument("--pds", default="https://bsky.social")
    ap.add_argument("--account-id", default="")
    a = ap.parse_args()

    import asyncio

    from sqlalchemy import select

    from app.core.security import decrypt_token, encrypt_token
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async def run():
        async with async_session_maker() as db:
            q = select(SocialAccount).where(
                SocialAccount.platform == "bluesky",
                SocialAccount.username == a.handle,
            )
            if a.account_id:
                q = q.where(SocialAccount.id == a.account_id)
            acc = (await db.execute(q)).scalars().first()
            if not acc:
                print(f"no bluesky account for {a.handle}")
                return 1
            stored = decrypt_token(acc.access_token_enc)
            with httpx.Client() as c:
                if probe(c, a.pds, a.handle, stored):
                    print("stored credential is healthy — nothing to do")
                    return 0
                print("stored app password dead (401) — renewing")
                if not a.main_password:
                    print("ERROR: --main-password required to mint a new app password")
                    return 2
                sess = probe(c, a.pds, a.handle, a.main_password)
                if not sess:
                    print("main password rejected — cannot mint")
                    return 2
                new_pw = mint_app_password(c, a.pds, sess["accessJwt"], "socialauto-renew")
                assert probe(c, a.pds, a.handle, new_pw), "fresh app password rejected"
                acc.access_token_enc = encrypt_token(new_pw)
                (acc.meta_data or {}).update({"credentials_configured": True})
                await db.commit()
                print("new app password minted, stored, verified")
                return 0

    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
