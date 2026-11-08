"""Coverage for gate_sweep — CDP probe, pending list, run paths."""

from types import SimpleNamespace

import app.worker.tasks.gate_sweep as G


def test_cdp_live_paths(monkeypatch):
    monkeypatch.setattr(G.httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200))
    assert G._cdp_live() is True
    monkeypatch.setattr(G.httpx, "get", lambda *a, **k: SimpleNamespace(status_code=502))
    assert G._cdp_live() is False
    monkeypatch.setattr(G.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(Exception("refused")))
    assert G._cdp_live() is False


def test_pending_paths(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="ok\nlinkedin-oauth pending\nfoo pending\n"))
    out = G._pending()
    assert len(out) == 2 and "pending" in out[0].lower()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(Exception("no file")))
    assert G._pending() == []


def test_run_gate_sweep_paths(monkeypatch):
    monkeypatch.setattr(G, "_cdp_live", lambda: False)
    assert G.run_gate_sweep() == {"skipped": "cdp offline"}
    monkeypatch.setattr(G, "_cdp_live", lambda: True)
    monkeypatch.setattr(G, "_pending", lambda: [])
    assert G.run_gate_sweep() == {"fired": 0}
    monkeypatch.setattr(G, "_pending", lambda: ["linkedin", "x"])
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="out", stderr="err"))
    out = G.run_gate_sweep()
    assert out["fired"] == 2 and "out" in out["out"]
