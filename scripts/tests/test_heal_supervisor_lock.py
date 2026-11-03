"""heal_supervisor.py deploy-lock contract + 'never restarts the stack'."""
import importlib.util
import os
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUP = REPO / ".devin/skills/pending-triggers-ops/scripts/heal_supervisor.py"


def _mod():
    spec = importlib.util.spec_from_file_location("heal_supervisor", SUP)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_lock_absent(tmp_path):
    assert _mod().deploy_locked(str(tmp_path / "nope"), 3600) is False


def test_lock_fresh_and_stale(tmp_path):
    m = _mod()
    lock = tmp_path / ".deploy.lock"
    lock.write_text("x")
    assert m.deploy_locked(str(lock), 3600) is True
    old = time.time() - 7200
    os.utime(lock, (old, old))
    assert m.deploy_locked(str(lock), 3600) is False


def test_supervisor_never_does_compose_wide_restarts():
    src = SUP.read_text()
    for bad in ("compose restart", "compose up", "compose down",
                '"compose", "restart"', '"compose", "up"', '"compose", "down"',
                "docker restart"):
        assert bad not in src, bad
