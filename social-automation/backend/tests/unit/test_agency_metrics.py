"""/agency/metrics control-board endpoint."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.api.agency as A
from app.worker import celery_app as CA


def _db(values):
    db = MagicMock()
    rs = []
    for v in values:
        r = MagicMock()
        r.scalar_one.return_value = v
        rs.append(r)
    db.execute = AsyncMock(side_effect=rs)
    return db


@pytest.mark.asyncio
async def test_metrics_happy(monkeypatch):
    monkeypatch.setattr(A, "_team_id", AsyncMock(return_value=uuid.uuid4()))
    insp = MagicMock()
    insp.active.return_value = {"w1": [1, 2], "w2": [3]}
    monkeypatch.setattr(CA.celery_app.control, "inspect", lambda: insp)
    out = await A.agency_metrics(user=SimpleNamespace(), db=_db([12.345, 10, 1, 42.26]))
    assert out == {"tcd_eur": 12.35, "tsr_pct": 90.0, "hipc_weekly": 0, "ppl_active": 3, "alv_minutes": 42.3, "period": "month_to_date"}


@pytest.mark.asyncio
async def test_metrics_empty_and_celery_down(monkeypatch):
    monkeypatch.setattr(A, "_team_id", AsyncMock(return_value=uuid.uuid4()))

    def boom():
        raise RuntimeError("broker down")

    monkeypatch.setattr(CA.celery_app.control, "inspect", boom)
    out = await A.agency_metrics(user=SimpleNamespace(), db=_db([None, 0, 0, None]))
    assert out["tcd_eur"] == 0.0 and out["tsr_pct"] == 100.0
    assert out["ppl_active"] == 0 and out["alv_minutes"] == 0.0


@pytest.mark.asyncio
async def test_metrics_inspect_none(monkeypatch):
    monkeypatch.setattr(A, "_team_id", AsyncMock(return_value=uuid.uuid4()))
    insp = MagicMock()
    insp.active.return_value = None
    monkeypatch.setattr(CA.celery_app.control, "inspect", lambda: insp)
    out = await A.agency_metrics(user=SimpleNamespace(), db=_db([1, 4, 1, 5]))
    assert out["ppl_active"] == 0 and out["tsr_pct"] == 75.0
