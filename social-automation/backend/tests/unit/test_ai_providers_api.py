"""Coverage for app/api/ai_providers.py."""
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.ai_providers as AIP
from app.models.ai_provider import AIProvider


def _user():
    return SimpleNamespace(id=uuid.uuid4(), email="a@b.c")


def _team():
    return SimpleNamespace(id=uuid.uuid4())


class _Scalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _Result:
    def __init__(self, scalars=None, one=None, rows=None, scalar=None):
        self._scalars = scalars
        self._one = one
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return _Scalars(self._scalars or [])

    def scalar_one_or_none(self):
        return self._one

    def all(self):
        return self._rows if self._rows is not None else []

    def scalar(self):
        return self._scalar


_MISSING = object()


class _DB:
    def __init__(self, results=None, team=_MISSING):
        self._q = list(results or [])
        self._team = _team() if team is _MISSING else team
        self.added = []
        self.deleted = []
        self.commits = 0

    async def get(self, model, key):
        return self._team

    async def execute(self, stmt, *a):
        return self._q.pop(0) if self._q else _Result()

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        # Simulate server-side column defaults landing after INSERT.
        for attr, default in (
            ("id", uuid.uuid4()),
            ("display_name", obj.name),
            ("base_url", ""),
            ("default_model", ""),
            ("is_enabled", False),
            ("is_default", False),
            ("api_key_enc", None),
            ("fallbacks", None),
            ("timeout_seconds", 120),
            ("max_retries", 1),
            ("daily_neuron_budget", None),
            ("failure_count", 0),
            ("circuit_open", False),
            ("cooldown_until", None),
            ("updated_at", datetime.now(UTC)),
        ):
            if getattr(obj, attr, None) is None:
                setattr(obj, attr, default)


def _provider(name="groq", **kw):
    p = AIProvider(team_id=uuid.uuid4(), name=name)
    for attr, default in (
        ("id", uuid.uuid4()),
        ("display_name", name.title()),
        ("api_key_enc", b"k"),
        ("base_url", "https://api"),
        ("default_model", "m1"),
        ("is_enabled", True),
        ("is_default", False),
        ("fallbacks", None),
        ("timeout_seconds", 120),
        ("max_retries", 1),
        ("daily_neuron_budget", None),
        ("failure_count", 0),
        ("circuit_open", False),
        ("cooldown_until", None),
        ("updated_at", datetime.now(UTC)),
    ):
        setattr(p, attr, default)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


class TestCatalog:
    @pytest.mark.asyncio
    async def test_returns_catalog(self):
        out = await AIP.list_catalog()
        assert out is AIP.PROVIDER_CATALOG
        assert isinstance(out, list)


class TestListProviders:
    @pytest.mark.asyncio
    async def test_team_missing(self):
        db = _DB(team=None)
        with pytest.raises(HTTPException) as ei:
            await AIP.list_providers(team_id=uuid.uuid4(),
                                     current_user=_user(), db=db)
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_lists(self):
        p = _provider("groq", api_key_enc=None)
        db = _DB(results=[_Result(scalars=[p])])
        out = await AIP.list_providers(team_id=uuid.uuid4(),
                                       current_user=_user(), db=db)
        assert len(out) == 1
        assert out[0].name == "groq"
        assert out[0].has_key is False


class TestUpsert:
    @pytest.mark.asyncio
    async def test_unknown_provider(self):
        db = _DB()
        body = AIP.AIProviderUpsert()
        with pytest.raises(HTTPException) as ei:
            await AIP.upsert_provider("bogus", body, team_id=uuid.uuid4(),
                                      current_user=_user(), db=db)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_new(self, monkeypatch):
        db = _DB(results=[_Result(one=None)])  # no existing provider
        monkeypatch.setattr(AIP, "encrypt_token", lambda s: b"enc:" + s.encode())
        log = AsyncMock()
        monkeypatch.setattr(AIP, "log_action", log)
        body = AIP.AIProviderUpsert(
            display_name="DMR", api_key="secret", base_url="http://dmr",
            default_model="llama", fallbacks="groq,cloudflare",
            timeout_seconds=30, max_retries=2, daily_neuron_budget=500)
        out = await AIP.upsert_provider("dmr", body, team_id=uuid.uuid4(),
                                        current_user=_user(), db=db)
        assert out.name == "dmr"
        assert out.display_name == "DMR"
        assert out.has_key
        assert out.fallbacks == "groq,cloudflare"
        assert out.timeout_seconds == 30
        assert out.max_retries == 2
        assert out.daily_neuron_budget == 500
        assert db.added and db.added[0].api_key_enc == b"enc:secret"
        log.assert_awaited_once()
        assert db.commits == 1

    @pytest.mark.asyncio
    async def test_update_existing_clear_key(self, monkeypatch):
        existing = _provider("groq")
        db = _DB(results=[_Result(one=existing)])
        monkeypatch.setattr(AIP, "log_action", AsyncMock())
        body = AIP.AIProviderUpsert(api_key="", display_name="Groq X")
        out = await AIP.upsert_provider("groq", body, team_id=uuid.uuid4(),
                                        current_user=_user(), db=db)
        assert existing.api_key_enc is None
        assert not out.has_key
        assert existing.display_name == "Groq X"

    @pytest.mark.asyncio
    async def test_set_default_clears_others(self, monkeypatch):
        existing = _provider("groq")
        other = _provider("nvidia", is_default=True)
        db = _DB(results=[_Result(one=existing),
                          _Result(scalars=[other])])
        monkeypatch.setattr(AIP, "log_action", AsyncMock())
        body = AIP.AIProviderUpsert(is_default=True)
        out = await AIP.upsert_provider("groq", body, team_id=uuid.uuid4(),
                                        current_user=_user(), db=db)
        assert out.is_default
        assert other.is_default is False


class TestProviderModels:
    @pytest.mark.asyncio
    async def test_non_cloudflare(self):
        with pytest.raises(HTTPException) as ei:
            await AIP.list_provider_models("groq", current_user=_user())
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_cloudflare(self, monkeypatch):
        import app.services.inference as inf
        monkeypatch.setattr(inf, "list_workers_ai_models",
                            AsyncMock(return_value={"models": ["m"]}))
        out = await AIP.list_provider_models("cloudflare",
                                             current_user=_user())
        assert out == {"models": ["m"]}


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, monkeypatch):
        p = _provider("groq")
        db = _DB(results=[_Result(one=p)])
        log = AsyncMock()
        monkeypatch.setattr(AIP, "log_action", log)
        await AIP.delete_provider("groq", team_id=uuid.uuid4(),
                                  current_user=_user(), db=db)
        assert db.deleted == [p]
        assert db.commits == 1
        log.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_missing_noop(self, monkeypatch):
        db = _DB(results=[_Result(one=None)])
        monkeypatch.setattr(AIP, "log_action", AsyncMock())
        await AIP.delete_provider("groq", team_id=uuid.uuid4(),
                                  current_user=_user(), db=db)
        assert db.deleted == [] and db.commits == 0


class TestTestProvider:
    @pytest.mark.asyncio
    async def test_invalid_name(self):
        with pytest.raises(HTTPException) as ei:
            await AIP.test_provider("bad/name", team_id=uuid.uuid4(),
                                    current_user=_user(), db=None)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_llm_ok(self, monkeypatch):
        import app.services.inference as inf
        call = AsyncMock(return_value={"text": "hello there"})
        monkeypatch.setattr(inf, "call_inference", call)
        out = await AIP.test_provider("groq", team_id=uuid.uuid4(),
                                      current_user=_user(), db=None)
        assert out == {"ok": True, "response": "hello there"}
        assert call.await_args.kwargs["model_override"] == "qwen/qwen3.6-27b"

    @pytest.mark.asyncio
    async def test_llm_error(self, monkeypatch):
        import app.services.inference as inf
        monkeypatch.setattr(inf, "call_inference",
                            AsyncMock(side_effect=RuntimeError("down")))
        out = await AIP.test_provider("groq", team_id=uuid.uuid4(),
                                      current_user=_user(), db=None)
        assert out["ok"] is False

    @pytest.mark.asyncio
    async def test_image_gen_ok(self, monkeypatch):
        import app.services.inference as inf
        monkeypatch.setattr(inf, "_call_workers_ai_image", AsyncMock(
            return_value={"image_bytes": b"png-bytes"}))
        out = await AIP.test_provider("local-sd35", team_id=uuid.uuid4(),
                                      current_user=_user(), db=None)
        assert out["ok"] is True
        assert "9 bytes" in out["response"]

    @pytest.mark.asyncio
    async def test_image_gen_error(self, monkeypatch):
        import app.services.inference as inf
        monkeypatch.setattr(inf, "_call_workers_ai_image",
                            AsyncMock(side_effect=RuntimeError()))
        out = await AIP.test_provider("nvidia-flux", team_id=uuid.uuid4(),
                                      current_user=_user(), db=None)
        assert out["ok"] is False


class TestUsageSummary:
    @pytest.mark.asyncio
    async def test_aggregates(self):
        now = datetime.now(UTC)
        db = _DB(results=[
            _Result(rows=[
                ("groq", "m1", 10, 8, 120.456, 300, 0.012345, now),
                ("cf", "m2", 2, None, None, None, None, None),
            ]),
            _Result(scalar=250),          # daily neurons
            _Result(scalar=1000),         # budget
        ])
        out = await AIP.get_usage_summary(team_id=uuid.uuid4(), days=7,
                                          current_user=_user(), db=db)
        assert len(out.summary) == 2
        s0 = out.summary[0]
        assert s0.total_calls == 10
        assert s0.failed_calls == 2
        assert s0.avg_latency_ms == 120.5
        assert s0.total_neurons == 300
        assert s0.estimated_cost == 0.0123
        assert s0.last_call_at == now
        # second row Nones → zeroed
        assert out.summary[1].successful_calls == 0
        assert out.daily_neurons == 250
        assert out.daily_neuron_budget == 1000
        assert out.quota_pct == 25.0

    @pytest.mark.asyncio
    async def test_no_budget(self):
        db = _DB(results=[
            _Result(rows=[]),
            _Result(scalar=None),
            _Result(scalar=None),
        ])
        out = await AIP.get_usage_summary(team_id=uuid.uuid4(), days=1,
                                          current_user=_user(), db=db)
        assert out.summary == []
        assert out.daily_neurons == 0
        assert out.quota_pct is None


class TestResetCircuit:
    @pytest.mark.asyncio
    async def test_not_found(self):
        db = _DB(results=[_Result(one=None)])
        with pytest.raises(HTTPException) as ei:
            await AIP.reset_circuit_breaker("groq", team_id=uuid.uuid4(),
                                            current_user=_user(), db=db)
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_resets(self):
        p = _provider("groq", failure_count=5, circuit_open=True,
                      cooldown_until=datetime.now(UTC))
        db = _DB(results=[_Result(one=p)])
        import app.services.inference as inf
        inf._circuit_state["groq"] = {"failures": 5}
        out = await AIP.reset_circuit_breaker("groq", team_id=uuid.uuid4(),
                                              current_user=_user(), db=db)
        assert out["ok"] is True
        assert p.failure_count == 0
        assert p.circuit_open is False
        assert p.cooldown_until is None
        assert "groq" not in inf._circuit_state
        assert db.commits == 1
