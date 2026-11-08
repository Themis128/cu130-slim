"""Live smoke test for the devapps lake exports + insights signals."""

import asyncio
import json

from app.db.session import async_session_maker
from app.services.datalake_insights import compute_devapp_signals
from app.worker.tasks.datalake_export import _export_channel_actions, _export_devapps


async def main() -> None:
    async with async_session_maker() as db:
        snaps = await _export_devapps(db)
        actions = await _export_channel_actions(db)
        print(f"devapps snapshots: {len(snaps)}")
        print(f"channel actions:   {len(actions)}")
        print("\nsample snapshot:", json.dumps(snaps[0], default=str)[:600] if snaps else "none")
        print("\nsample action:", json.dumps(actions[0], default=str)[:400] if actions else "none")

        signals = compute_devapp_signals(snaps)
        print("\n=== compute_devapp_signals ===")
        print(json.dumps(signals, indent=2, default=str)[:3000])


asyncio.run(main())
