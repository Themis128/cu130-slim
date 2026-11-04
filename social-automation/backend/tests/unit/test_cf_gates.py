"""Coverage for ``app.api.cf_gates`` — CF paid-workers gate router."""

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api import cf_gates as mod


class _Proc:
    def __init__(self, rc=0):
        self.returncode = rc
        self.stdout = "out"
        self.stderr = "err"


def _user():
    return MagicMock()


def test_run_missing(monkeypatch):
    monkeypatch.setattr(mod, "SCRIPTS", mod.Path("/nonexistent"))
    with pytest.raises(HTTPException) as e:
        mod._run("nope.py")
    assert e.value.status_code == 404


def test_run_timeout(monkeypatch, tmp_path):
    (tmp_path / "s.py").write_text("pass")
    monkeypatch.setattr(mod, "SCRIPTS", tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", MagicMock(side_effect=subprocess.TimeoutExpired("x", 1)))
    with pytest.raises(HTTPException) as e:
        mod._run("s.py")
    assert e.value.status_code == 504


def test_run_ok_and_nonzero(monkeypatch, tmp_path):
    (tmp_path / "s.py").write_text("pass")
    monkeypatch.setattr(mod, "SCRIPTS", tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc(0))
    out = mod._run("s.py")
    assert out["ok"] and out["exit"] == 0 and out["stdout"] == "out"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc(3))
    assert not mod._run("s.py")["ok"]


@pytest.mark.asyncio
async def test_gate_status(monkeypatch, tmp_path):
    (tmp_path / "cf_workers_doctor.py").write_text("pass")
    monkeypatch.setattr(mod, "SCRIPTS", tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc(0))
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    out = await mod.gate_status(_user())
    assert out["token_present"] is False and out["doctor"]["ok"]
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setattr(mod, "SCRIPTS", mod.Path("/nonexistent"))
    out = await mod.gate_status(_user())
    assert out["token_present"] and not out["doctor"]["ok"]


@pytest.mark.asyncio
async def test_run_gate_step_unknown():
    with pytest.raises(HTTPException) as e:
        await mod.run_gate_step("bogus", _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_run_gate_step_ok(monkeypatch, tmp_path):
    (tmp_path / "cf_logpush_setup.py").write_text("pass")
    monkeypatch.setattr(mod, "SCRIPTS", tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc(0))
    out = await mod.run_gate_step("logpush", _user())
    assert out["ok"]


@pytest.mark.asyncio
async def test_accept_token_short():
    with pytest.raises(HTTPException) as e:
        await mod.accept_token({"token": "short"}, _user())
    assert e.value.status_code == 400


def _resp(payload):
    class R:
        def read(self):
            return json.dumps(payload).encode()
    return R()


@pytest.mark.asyncio
async def test_accept_token_verify_fails(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", MagicMock(side_effect=OSError("x")))
    with pytest.raises(HTTPException) as e:
        await mod.accept_token({"token": "t" * 40}, _user())
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_accept_token_rejected(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _resp({"success": False}))
    with pytest.raises(HTTPException) as e:
        await mod.accept_token({"token": "t" * 40}, _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_accept_token_ok(monkeypatch, tmp_path):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _resp({"success": True}))
    monkeypatch.setattr(mod, "SCRIPTS", tmp_path)
    env = tmp_path / ".env"
    env.write_text("CLOUDFLARE_API_TOKEN=old\nOTHER=1\n")
    monkeypatch.setattr(mod.Path, "exists", lambda self: True)
    # keep the real script files absent → _run 404s → still exercises chain dict
    for name in mod.TOOLS.values():
        (tmp_path / name).write_text("pass")
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc(0))
    real_path = mod.Path
    monkeypatch.setattr(mod, "Path", lambda p: env if str(p) == "/app/.env" else real_path(p))
    tok = "t" * 40
    out = await mod.accept_token({"token": tok}, _user())
    assert out["token"] == "verified+stored"
    assert set(out["chain"]) == {"destination", "maildir-confirm", "logpush"}
    assert f"CLOUDFLARE_API_TOKEN={tok}" in env.read_text()
