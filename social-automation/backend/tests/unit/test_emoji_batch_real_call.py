"""generate_emoji_batch must hand each concept to the real generate_emoji
with the right team/user/db — the endpoint suite mocks generate_emoji, which
hid an argument-order bug that failed every item in production."""

from unittest.mock import AsyncMock

import pytest

from app.api import ai
from app.api.ai import EmojiBatchRequest
from tests.unit.test_ai_api_router import _DB, _REQ, _limiter_off, _team, _user  # noqa: F401 — autouse


@pytest.mark.asyncio
async def test_batch_runs_real_generate_emoji_per_concept(monkeypatch):
    import app.services.dmr as dmr
    import app.services.inference as inf

    monkeypatch.setattr(ai, "check_quota", AsyncMock())
    local = AsyncMock(side_effect=[{"image_base64": "QQ=="}, {"image_base64": "Qg=="}])
    monkeypatch.setattr(inf, "_call_local_diffusers_txt2img", local)
    monkeypatch.setattr(dmr, "call_dmr_vision", AsyncMock(return_value=None))
    team = _team()
    db = _DB(team=team)
    out = await ai.generate_emoji_batch(
        _REQ,
        EmojiBatchRequest(concepts=["rocket", "cloud"], background="white", remove_bg=False, enhance_prompt=False),
        team.id,
        current_user=_user(),
        db=db,
    )
    assert [e["success"] for e in out.emojis] == [True, True], out.emojis
    assert [e["image_base64"] for e in out.emojis] == ["QQ==", "Qg=="]
    assert all(e["provider"] == "local-diffusers" for e in out.emojis)
    # batch-level quota once + one per generated emoji, all against the team's db
    assert ai.check_quota.await_count == 3
    assert all(c.args[1] == team.id and c.args[2] is db for c in ai.check_quota.await_args_list)
    assert [c.kwargs["prompt"].split(",")[0] for c in local.await_args_list] == ["rocket", "cloud"]
