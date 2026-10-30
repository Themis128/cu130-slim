#!/usr/bin/env python3
"""Poll the live TikTok status of recent uploads.

Lists post_targets rows for the TikTok account that carry a publish_id,
then calls status/fetch for each via the in-container TikTokAPIClient.
Shows DB status vs live status so stale 'published' rows that are actually
SEND_TO_USER_INBOX drafts are visible.

Usage: check-pending.py [--cancel]
  --cancel   also cancel rows still in SEND_TO_USER_INBOX (frees the
             pending-share window). Requires explicit opt-in — destructive.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "_lib"))
from skill_http import repo_root  # noqa: E402

CANCEL = "--cancel" in sys.argv

POLL_PY = f'''
import asyncio
from sqlalchemy import text
from app.db.session import async_session_maker
from app.services.tiktok_api import TikTokAPIClient
from app.core.security import decrypt_token, decrypt_field

CANCEL = {CANCEL!r}

async def main():
    async with async_session_maker() as db:
        acct = (await db.execute(text(
            "select access_token_enc, account_id from social_accounts "
            "where platform='tiktok' limit 1"))).first()
        if not acct:
            print("no tiktok account")
            return
        try:
            plain = decrypt_token(acct[0])
        except Exception:
            plain = decrypt_field(acct[0])
        client = TikTokAPIClient(access_token=plain, open_id=acct[1])
        rows = (await db.execute(text(
            "select t.post_id, t.platform_post_id, t.status, t.published_at "
            "from post_targets t join social_accounts a "
            "on a.id=t.social_account_id "
            "where a.platform='tiktok' and t.platform_post_id is not null "
            "order by t.published_at desc nulls last limit 30"))).all()
        pending = 0
        for post_id, pid, db_status, pub_at in rows:
            try:
                st = await client.check_publish_status(pid)
                live = (st.get("data") or {{}}).get("status", "?")
            except Exception as e:
                print(f"{{str(post_id)[:8]}} {{pid[:24]:24}} db={{db_status}} live=ERR:{{type(e).__name__}}")
                continue
            flag = ""
            if live == "SEND_TO_USER_INBOX":
                pending += 1
                if CANCEL:
                    try:
                        await client.cancel_publish(pid)
                        flag = " -> CANCELLED"
                    except Exception as e:
                        flag = f" -> cancel_err:{{e}}"
                else:
                    flag = " (pending inbox confirm)"
            print(f"{{str(post_id)[:8]}} {{pid[:24]:24}} db={{db_status}} live={{live}}{{flag}}")
        print(f"\\n{{pending}} draft(s) pending inbox confirmation")
        if pending and not CANCEL:
            print("re-run with --cancel to free the pending-share window")

asyncio.run(main())
'''

sys.exit(subprocess.run(
    ["docker", "compose", "exec", "-T", "social-api",
     "python3", "-c", POLL_PY], cwd=repo_root()).returncode)
