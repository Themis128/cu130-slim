"""Live smoke test for devapps_sync — run inside the backend image/network.

Usage: python smoke_devapps.py [platform ...]
Writes real devapp_snapshot AnalyticsEvent rows (the intended output).
"""

import asyncio
import json
import sys

from sqlalchemy import select

from app.db.session import async_session_maker
from app.models.analytics import AnalyticsEvent
from app.models.social_account import SocialAccount
from app.services.devapps_sync import DEVAPP_EVENT_TYPE, build_channel_actions, sync_all_devapps


async def main() -> None:
    platforms = sys.argv[1:] or [None]
    async with async_session_maker() as db:
        for platform in platforms:
            print(f"\n=== sync_all_devapps(platform={platform!r}) ===", flush=True)
            res = await sync_all_devapps(db, platform=platform)
            print(
                json.dumps(
                    {
                        "accounts": res["accounts"],
                        "ok": res["ok"],
                        "failed": res["failed"],
                        "per_account": [
                            {
                                "platform": r.get("platform"),
                                "username": r.get("username"),
                                "ok": r.get("ok"),
                                "error": (r.get("error") or "")[:120],
                            }
                            for r in res["results"]
                        ],
                    },
                    indent=2,
                    default=str,
                )
            )

        # verify persisted events
        q = select(AnalyticsEvent).where(AnalyticsEvent.event_type == DEVAPP_EVENT_TYPE).order_by(AnalyticsEvent.created_at.desc()).limit(50)
        events = (await db.execute(q)).scalars().all()
        print(f"\n=== persisted devapp_snapshot events (latest {len(events)}) ===")
        for e in events[:15]:
            md = e.meta_data or {}
            print(
                f"  {e.occurred_at} {e.platform} acct={e.social_account_id} ok={md.get('ok')} "
                f"followers={((md.get('insights') or {}).get('followers'))} err={(md.get('error') or '')[:80]}"
            )

        # channel actions build (pure — feed latest snapshot per account)
        latest: dict = {}
        for e in events:
            key = str(e.social_account_id)
            if key not in latest:
                latest[key] = e.meta_data or {}
        actions = build_channel_actions(list(latest.values()))
        print(f"\n=== channel actions: {len(actions)} ===")
        for a in actions[:20]:
            print(" ", json.dumps(a, default=str)[:220])


asyncio.run(main())
