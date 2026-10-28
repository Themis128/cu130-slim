"""Real-code tests for small worker tasks + services.
Fakes only at boundaries: task sessions, service singletons, papermill,
socket.getaddrinfo, Celery run_async.
"""

from __future__ import annotations

import asyncio
import socket as _socket
import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.brand_voice as BV
import app.services.notebook_runner as NR
import app.worker.tasks.analytics as AT
import app.worker.tasks.digest as DG
import app.worker.tasks.linkedin_ads_control as LAC
import app.worker.tasks.linkedin_invites as LI
from app.services import pillars as PL
from app.services import url_safety as US


class _ACM:
    """async-context-manager session wrapper like task_session."""

    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows), first=lambda: self._rows[0] if self._rows else None)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _DB:
    def __init__(self, queues=None):
        self.queues = list(queues or [])
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt):
        return _Res(self.queues.pop(0) if self.queues else [])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


# ---------------------------------------------------------------------------
# app.worker.tasks.analytics
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_analytics_sync_all_teams(monkeypatch):
    t1, t2 = SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())
    db = _DB([[t1, t2]])
    monkeypatch.setattr(AT, "_worker_db", lambda: _ACM(db))
    res = SimpleNamespace(synced=5, errors=["e1"])
    monkeypatch.setattr(AT, "sync_team_analytics", AsyncMock(return_value=res))
    out = await AT._sync_all_analytics_async()
    assert out["teams"] == 2 and out["synced"] == 10 and out["errors"] == ["e1", "e1"]


@pytest.mark.asyncio
async def test_analytics_sync_all_teams_error_rolls_back(monkeypatch):
    t1 = SimpleNamespace(id=uuid.uuid4())
    db = _DB([[t1]])
    monkeypatch.setattr(AT, "_worker_db", lambda: _ACM(db))
    monkeypatch.setattr(AT, "sync_team_analytics", AsyncMock(side_effect=RuntimeError("boom")))
    out = await AT._sync_all_analytics_async()
    assert out["teams"] == 1 and out["synced"] == 0
    assert out["errors"] == [f"team {t1.id}: boom"]
    assert db.rollbacks == 1


def test_analytics_sync_all_task_wrapper(monkeypatch):
    t1 = SimpleNamespace(id=uuid.uuid4())
    db = _DB([[t1]])
    monkeypatch.setattr(AT, "_worker_db", lambda: _ACM(db))
    res = SimpleNamespace(synced=2, errors=[])
    monkeypatch.setattr(AT, "sync_team_analytics", AsyncMock(return_value=res))
    out = AT.sync_all_analytics()
    assert out["teams"] == 1 and out["synced"] == 2


def test_analytics_sync_team_task_wrapper(monkeypatch):
    db = _DB()
    monkeypatch.setattr(AT, "_worker_db", lambda: _ACM(db))
    res = SimpleNamespace(synced=1, skipped=0, errors=[], snapshots=[])
    monkeypatch.setattr(AT, "sync_team_analytics", AsyncMock(return_value=res))
    out = AT.sync_team_analytics_task(str(uuid.uuid4()))
    assert out["synced"] == 1


@pytest.mark.asyncio
async def test_analytics_sync_single_team(monkeypatch):
    db = _DB()
    monkeypatch.setattr(AT, "_worker_db", lambda: _ACM(db))
    res = SimpleNamespace(synced=3, skipped=1, errors=[], snapshots=["s"] * 60)
    fake = AsyncMock(return_value=res)
    monkeypatch.setattr(AT, "sync_team_analytics", fake)
    out = await AT._sync_team_async(str(uuid.uuid4()), 30)
    assert out["synced"] == 3 and out["skipped"] == 1 and len(out["snapshots"]) == 50
    assert fake.await_args.kwargs["days"] == 30


# ---------------------------------------------------------------------------
# app.worker.tasks.linkedin_ads_control
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ads_control_status_path(monkeypatch):
    db = _DB()
    monkeypatch.setattr(LAC, "task_session", lambda: _ACM(db))
    metrics = SimpleNamespace(campaign_name="Camp", campaign_id="c1", status="ACTIVE", spend_eur=12.5, clicks=30, ctr=1.5)
    monkeypatch.setattr(LAC, "collect_metrics", AsyncMock(return_value=metrics))
    notify = AsyncMock()
    monkeypatch.setattr(LAC, "notify_slack", notify)
    out = await LAC._run("status", "https://hooks.slack/resp", "u1")
    assert out == {"ok": True, "status": "ACTIVE"}
    text = notify.await_args.args[0]
    assert "Camp" in text and "ACTIVE" in text and "€12.50" in text
    assert notify.await_args.args[1] == "https://hooks.slack/resp"


@pytest.mark.asyncio
async def test_ads_control_pause_and_resume_paths(monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr(LAC, "notify_slack", notify)
    set_status = AsyncMock(return_value={"ok": True, "detail": "campaign paused"})
    monkeypatch.setattr(LAC, "set_campaign_status", set_status)
    out = await LAC._run("pause", None, "themis")
    assert out["ok"] is True
    assert "paused by @themis" in notify.await_args.args[0]
    assert ":pause_button:" in notify.await_args.args[0]
    set_status.return_value = {"ok": True, "detail": "resumed"}
    await LAC._run("resume", None, "themis")
    assert ":arrow_forward:" in notify.await_args.args[0]
    set_status.return_value = {"ok": False, "detail": "api error"}
    out = await LAC._run("pause", None, "themis")
    assert out["ok"] is False
    assert "Couldn't pause" in notify.await_args.args[0]


# ---------------------------------------------------------------------------
# app.worker.tasks.digest
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_digest_aggregates_team_reports(monkeypatch):
    db = _DB()
    monkeypatch.setattr(DG, "_worker_db", lambda: _ACM(db))
    reports = [
        {"team_name": "a", "posted_to_slack": True, "emailed": True, "issues": [1, 2]},
        {"team_name": "b", "posted_to_slack": False, "emailed": False, "slack_error": "hook dead", "issues": []},
    ]
    monkeypatch.setattr(DG, "run_daily_digest_for_all_teams", AsyncMock(return_value=reports))
    out = await DG._send_digest_async(days=1, post_to_slack=True, post_to_email=True)
    assert out["teams"] == 2 and out["posted_slack"] == 1 and out["emailed"] == 1
    assert out["errors"] == ["hook dead"]
    assert out["issue_counts"][0] == {"team": "a", "issues": 2}


def test_digest_task_wrappers(monkeypatch):
    db = _DB()
    monkeypatch.setattr(DG, "_worker_db", lambda: _ACM(db))
    monkeypatch.setattr(
        DG,
        "run_daily_digest_for_all_teams",
        AsyncMock(return_value=[{"posted_to_slack": True, "emailed": False, "issues": []}]),
    )
    out = DG.send_daily_slack_digest()
    assert out["teams"] == 1 and out["posted_slack"] == 1
    out = DG.send_weekly_slack_digest()
    assert out["teams"] == 1


def test_strategy_task_wrapper(monkeypatch):
    db = _DB()
    monkeypatch.setattr(DG, "_worker_db", lambda: _ACM(db))
    mod = types.ModuleType("app.services.strategy_report")
    mod.run_strategy_report_for_all_teams = AsyncMock(return_value=[{"emailed": True, "llm_used": False}])
    monkeypatch.setitem(sys.modules, "app.services.strategy_report", mod)
    out = DG.send_daily_strategy_report()
    assert out["teams"] == 1 and out["emailed"] == 1


@pytest.mark.asyncio
async def test_strategy_report_aggregation(monkeypatch):
    db = _DB()
    monkeypatch.setattr(DG, "_worker_db", lambda: _ACM(db))
    mod = types.ModuleType("app.services.strategy_report")
    mod.run_strategy_report_for_all_teams = AsyncMock(
        return_value=[
            {"emailed": True, "llm_used": True},
            {"emailed": False, "llm_used": False, "email_error": "smtp"},
        ]
    )
    monkeypatch.setitem(sys.modules, "app.services.strategy_report", mod)
    out = await DG._send_strategy_async(insight_days=30, send=True)
    assert out["teams"] == 2 and out["emailed"] == 1 and out["llm_used"] == 1
    assert out["errors"] == ["smtp"]


# ---------------------------------------------------------------------------
# app.services.brand_voice
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_brand_voice_empty_and_whitespace_samples():
    assert await BV.analyze_brand_voice([]) == {}
    assert await BV.analyze_brand_voice(["", "   "]) == {}


@pytest.mark.asyncio
async def test_brand_voice_calls_inference_and_unwraps_response(monkeypatch):
    captured = {}

    async def fake_inference(**kw):
        captured.update(kw)
        return {"response": {"tone_dimensions": {"formality": 3}}}

    monkeypatch.setattr(BV, "call_inference", fake_inference)
    out = await BV.analyze_brand_voice(["sample one", "sample two"])
    assert out == {"tone_dimensions": {"formality": 3}}
    assert captured["provider_name"] == "dmr"
    assert "sample one" in captured["prompt"] and "sample two" in captured["prompt"]


@pytest.mark.asyncio
async def test_brand_voice_returns_raw_result_without_response_key(monkeypatch):
    monkeypatch.setattr(BV, "call_inference", AsyncMock(return_value={"voice_signature": {"tone": "x"}}))
    out = await BV.analyze_brand_voice(["copy"])
    assert out == {"voice_signature": {"tone": "x"}}


@pytest.mark.asyncio
async def test_brand_voice_inference_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(BV, "call_inference", AsyncMock(side_effect=RuntimeError("down")))
    assert await BV.analyze_brand_voice(["copy"]) == {}


# ---------------------------------------------------------------------------
# app.services.notebook_runner
# ---------------------------------------------------------------------------
def test_notebook_runner_missing_notebook(tmp_path, monkeypatch):
    monkeypatch.setattr(NR, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(NR, "OUTPUT_DIR", tmp_path / "out")
    with pytest.raises(FileNotFoundError):
        NR.run_report_notebook("nope")


def test_notebook_runner_executes_and_reads_manifest(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    outdir = tmp_path / "out"
    reports.mkdir(parents=True)
    (reports / "rep.ipynb").write_text("{}")
    monkeypatch.setattr(NR, "REPORTS_DIR", reports)
    monkeypatch.setattr(NR, "OUTPUT_DIR", outdir)
    pm = types.ModuleType("papermill")

    def exec_nb(src, out, parameters=None, cwd=None, **kw):
        import json as _json
        from pathlib import Path as _P

        _P(parameters["manifest_path"]).write_text(_json.dumps({"subject": "S"}))
        _P(out).write_text("{}")

    pm.execute_notebook = exec_nb
    monkeypatch.setitem(sys.modules, "papermill", pm)
    out = NR.run_report_notebook("rep", {"extra": 1}, cwd=str(tmp_path))
    assert out["manifest"] == {"subject": "S"}
    assert out["notebook"].endswith(".ipynb")
    assert out["duration_s"] >= 0


def test_notebook_runner_no_manifest_warns(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    outdir = tmp_path / "out"
    reports.mkdir(parents=True)
    (reports / "rep.ipynb").write_text("{}")
    monkeypatch.setattr(NR, "REPORTS_DIR", reports)
    monkeypatch.setattr(NR, "OUTPUT_DIR", outdir)
    pm = types.ModuleType("papermill")
    pm.execute_notebook = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "papermill", pm)
    out = NR.run_report_notebook("rep")
    assert out["manifest"] == {}


# ---------------------------------------------------------------------------
# app.services.url_safety
# ---------------------------------------------------------------------------
def test_url_safety_scheme_and_host_guards():
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("")
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("ftp://x.com/f")
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("javascript:alert(1)")
    for host in ["localhost", "foo.localhost", "x.local", "metadata.google.internal", "y.internal"]:
        with pytest.raises(US.UnsafeUrlError):
            US.validate_public_http_url(f"https://{host}/")
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("https://user:pass@example.com/")
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("https://127.0.0.1/")
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("https://10.1.2.3/")
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("https://[::1]/")


def test_url_safety_normalizes_and_allows_public(monkeypatch):
    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **kw: [(None, None, None, "", ("93.184.216.34", 443))],
    )
    out = US.validate_public_http_url("example.com/path?q=1#frag")
    assert out == "https://example.com/path?q=1"
    out = US.validate_public_http_url("http://example.com:8080/a", allow_http=True)
    assert out == "http://example.com:8080/a"
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("http://example.com/a", allow_http=False)
    # literal public IP skips DNS
    assert US.validate_public_http_url("https://93.184.216.34/x") == "https://93.184.216.34/x"


def test_url_safety_dns_failures_and_private_resolutions(monkeypatch):
    def gai_err(*a, **kw):
        raise _socket.gaierror("no host")

    monkeypatch.setattr(_socket, "getaddrinfo", gai_err)
    with pytest.raises(US.UnsafeUrlError, match="Could not resolve"):
        US.validate_public_http_url("https://nonexistent.invalid/")
    monkeypatch.setattr(_socket, "getaddrinfo", lambda *a, **kw: [])
    with pytest.raises(US.UnsafeUrlError, match="Could not resolve"):
        US.validate_public_http_url("https://nonexistent.invalid/")
    # resolves to a private IP → rejected
    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **kw: [(None, None, None, "", ("192.168.1.1", 443))],
    )
    with pytest.raises(US.UnsafeUrlError, match="non-public"):
        US.validate_public_http_url("https://internal.example.com/")
    # unparseable sockaddr → rejected
    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **kw: [(None, None, None, "", ("not-an-ip", 443))],
    )
    with pytest.raises(US.UnsafeUrlError, match="non-public"):
        US.validate_public_http_url("https://weird.example.com/")
    # mixed: one public + one private → rejected
    monkeypatch.setattr(
        _socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (None, None, None, "", ("93.184.216.34", 443)),
            (None, None, None, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(US.UnsafeUrlError):
        US.validate_public_http_url("https://mixed.example.com/")


# ---------------------------------------------------------------------------
# app.worker.tasks.linkedin_invites
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_log_invite_event_skips_no_signal():
    # no sent status + not no_credits → early return, no DB touch
    await LI._log_invite_event({"status": "error", "reason": "bridge_down"})


@pytest.mark.asyncio
async def test_log_invite_event_records_send(monkeypatch):
    import app.services.growth_initiatives as GI

    rec = AsyncMock()
    monkeypatch.setattr(GI, "record_initiative_event", rec)
    team_id = uuid.uuid4()
    acct_id = uuid.uuid4()
    db = _DB([[SimpleNamespace(id=team_id)], [acct_id], []])  # teams, acct, no dup
    monkeypatch.setattr(LI, "_worker_db", lambda: _ACM(db))
    await LI._log_invite_event({"status": "sent", "sent": 5, "credits_available": 40})
    rec.assert_awaited_once()
    kw = rec.await_args.kwargs
    assert kw["units"] == 5 and kw["credits_left"] == 35
    assert kw["note"] == "auto daily batch" and kw["platform"] == "linkedin"


@pytest.mark.asyncio
async def test_log_invite_event_no_credits_path(monkeypatch):
    import app.services.growth_initiatives as GI

    rec = AsyncMock()
    monkeypatch.setattr(GI, "record_initiative_event", rec)
    db = _DB([[SimpleNamespace(id=uuid.uuid4())], [uuid.uuid4()], []])
    monkeypatch.setattr(LI, "_worker_db", lambda: _ACM(db))
    await LI._log_invite_event({"status": "skipped", "reason": "no_credits"})
    kw = rec.await_args.kwargs
    assert kw["units"] == 0 and kw["credits_left"] == 0


@pytest.mark.asyncio
async def test_log_invite_event_skips_without_linkedin_account_and_dupes(monkeypatch):
    import app.services.growth_initiatives as GI

    rec = AsyncMock()
    monkeypatch.setattr(GI, "record_initiative_event", rec)
    # team1: no linkedin account → skip; team2: dup exists → skip
    db = _DB(
        [
            [SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4())],
            [None],  # team1 acct query → None
            [uuid.uuid4()],  # team2 acct
            [(uuid.uuid4(),)],  # team2 dup check → exists
        ]
    )
    monkeypatch.setattr(LI, "_worker_db", lambda: _ACM(db))
    await LI._log_invite_event({"status": "sent", "sent": 3, "credits_available": 10})
    rec.assert_not_awaited()


def test_send_linkedin_invites_task(monkeypatch):
    import app.services.linkedin_invites as svc

    svc.send_invite_batch = AsyncMock(return_value={"status": "sent", "sent": 2, "credits_available": 9})

    def runner(coro):
        return asyncio.run(coro)

    monkeypatch.setattr(LI, "_run_async", runner)
    import app.services.growth_initiatives as GI

    monkeypatch.setattr(GI, "record_initiative_event", AsyncMock())
    db = _DB(
        [
            [],
        ]
    )  # no teams → no event writes
    monkeypatch.setattr(LI, "_worker_db", lambda: _ACM(db))
    out = LI.send_linkedin_invites(batch_size=10)
    assert out == {"status": "sent", "sent": 2, "credits_available": 9}


@pytest.mark.asyncio
async def test_run_async_inside_running_loop_uses_thread():
    async def coro():
        return {"v": 1}

    assert LI._run_async(coro()) == {"v": 1}


@pytest.mark.asyncio
async def test_run_async_thread_propagates_error():
    async def coro():
        raise RuntimeError("inner")

    # Cross-thread run_until_complete surfaces the failure as RuntimeError
    # (asyncio reports the coroutine-reuse error rather than the original).
    with pytest.raises(RuntimeError):
        LI._run_async(coro())


def test_ads_control_task_wrapper(monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr(LAC, "notify_slack", notify)
    monkeypatch.setattr(LAC, "set_campaign_status", AsyncMock(return_value={"ok": True, "detail": "d"}))
    out = LAC.linkedin_ads_control("resume", "https://hooks.slack/r", "u")
    assert out["ok"] is True


def test_send_linkedin_invites_task_logging_failure_never_breaks(monkeypatch):
    import app.services.linkedin_invites as svc

    svc.send_invite_batch = AsyncMock(return_value={"status": "sent", "sent": 2, "credits_available": 9})

    def runner(coro):
        return asyncio.run(coro)

    monkeypatch.setattr(LI, "_run_async", runner)
    monkeypatch.setattr(LI, "_log_invite_event", AsyncMock(side_effect=RuntimeError("db dead")))
    out = LI.send_linkedin_invites()
    assert out["status"] == "sent"  # send result returned despite logging failure


# ---------------------------------------------------------------------------
# app.services.pillars
# ---------------------------------------------------------------------------
def _pillar(name, sort_order=0, pid=None):
    return SimpleNamespace(id=pid or uuid.uuid4(), name=name, sort_order=sort_order)


def test_classify_pillar_heuristics():
    testing = _pillar("Testing")
    proof = _pillar("Proof")
    perspective = _pillar("Perspective")
    pillars = [testing, proof, perspective]
    assert PL.classify_pillar("", pillars) is None
    assert PL.classify_pillar("I tried this experiment and the results were 40% faster", pillars) is testing
    assert PL.classify_pillar("Here's how to migrate step-by-step — a guide", pillars) is proof
    assert PL.classify_pillar("You don't need the enterprise plan — what matters is discipline", pillars) is perspective
    assert PL.classify_pillar("a completely neutral sentence with zero signals", pillars) is None
    # unknown pillar names simply don't match
    assert PL.classify_pillar("I tested it", [_pillar("Unknown")]) is None
    # strongest match wins — more testing hints than proof
    p = PL.classify_pillar("how to test: I tested the benchmark, results saved €50", pillars)
    assert p is testing


@pytest.mark.asyncio
async def test_least_covered_and_resolve_pillar(monkeypatch):
    p_low = _pillar("Proof", sort_order=0)
    p_high = _pillar("Testing", sort_order=1)
    team = uuid.uuid4()

    class _DBCounts(_DB):
        async def execute(self, stmt):
            return _Res([(p_low, 0), (p_high, 3)])

    out = await PL.least_covered_pillar(_DBCounts(), team)
    assert out is p_low

    class _DBEmpty(_DB):
        async def execute(self, stmt):
            return _Res([])

    assert await PL.least_covered_pillar(_DBEmpty(), team) is None
    # resolve_pillar paths
    assert await PL.resolve_pillar(_DBEmpty(), team, None) is None
    assert await PL.resolve_pillar(_DBEmpty(), team, "  ") is None

    class _DBAuto(_DBCounts):
        pass

    got = await PL.resolve_pillar(_DBAuto(), team, "auto")
    assert got is p_low

    class _DBByName(_DB):
        async def execute(self, stmt):
            return _Res([p_high])

    assert await PL.resolve_pillar(_DBByName(), team, "  testing ") is p_high
    assert await PL.resolve_pillar(_DBByName(), team, str(p_high.id)) is p_high


# ---------------------------------------------------------------------------
# app.main — route handlers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_main_health_and_favicon(monkeypatch):
    import app.main as M

    assert (await M.favicon()).status_code == 204
    out = await M.health_check()
    assert out["status"] == "ok" and out["service"] == M.settings.APP_NAME


@pytest.mark.asyncio
async def test_main_metrics_endpoint(monkeypatch):
    import app.main as M
    from app.services import metrics as svc_metrics

    monkeypatch.setattr(svc_metrics, "refresh_business_metrics", AsyncMock())
    monkeypatch.setattr(svc_metrics, "metrics_response", MagicMock(return_value=b"m"))
    res = await M.prometheus_metrics()
    assert res.status_code == 200
    assert res.body == b"m"


@pytest.mark.asyncio
async def test_main_lifespan_startup_shutdown(monkeypatch):
    import app.main as M

    dmr = types.ModuleType("app.services.dmr")
    dmr.on_startup = AsyncMock()
    dmr.on_shutdown = AsyncMock()
    monkeypatch.setitem(sys.modules, "app.services.dmr", dmr)
    monkeypatch.setattr(M, "register_route_metrics", MagicMock())
    monkeypatch.setattr(M, "init_db", AsyncMock())
    async with M.lifespan(M.app):
        pass
    dmr.on_startup.assert_awaited_once()
    dmr.on_shutdown.assert_awaited_once()
