"""Tail coverage — openapi endpoints + model helpers."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_openapi_endpoints():
    import app.main as M
    assert callable(M.openapi_root) and callable(M.openapi_prefixed)
    out = await M.openapi_root()
    assert isinstance(out, dict) and "openapi" in out
    out2 = await M.openapi_prefixed()
    assert isinstance(out2, dict) and "openapi" in out2


def test_agency_utcnow():
    from datetime import datetime

    from app.models.agency import _utcnow
    assert isinstance(_utcnow(), datetime)


def test_ai_function_call_sha():
    from app.models.ai_function_call import _sha
    s = _sha("hello")
    assert len(s) == 32 and s == _sha("hello") and s != _sha("bye")
