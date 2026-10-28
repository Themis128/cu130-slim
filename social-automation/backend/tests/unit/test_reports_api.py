"""Unit tests for app.api.reports — list/serve/run for notebook reports.

Endpoints are invoked directly; OUTPUT_DIR/REPORTS_DIR are monkeypatched to a
tmp dir, Celery send is faked. No network, no database.
"""
import json

import pytest
from fastapi import HTTPException

from app.api import reports as rep_api


@pytest.fixture()
def report_dirs(tmp_path, monkeypatch):
    out = tmp_path / "output"
    reps = tmp_path / "reports"
    out.mkdir()
    reps.mkdir()
    # Notebook names are used to split run prefixes — the two real reports.
    (reps / "daily_strategy_brief.ipynb").write_text("{}")
    (reps / "linkedin_ads_daily.ipynb").write_text("{}")
    monkeypatch.setattr(rep_api, "OUTPUT_DIR", out)
    monkeypatch.setattr(rep_api, "REPORTS_DIR", reps)
    return out, reps


def _write_run(out, name="daily_strategy_brief", run_id="20261003-103000"):
    prefix = f"{name}-{run_id}"
    (out / f"{prefix}.ipynb").write_text("{}")
    (out / f"brief-{run_id}-0.html").write_text("<html>report</html>")
    (out / f"brief-{run_id}-0.txt").write_text("report text")
    manifest = {
        "reports": [
            {
                "subject": "Strategy brief",
                "html_file": str(out / f"brief-{run_id}-0.html"),
                "text_file": str(out / f"brief-{run_id}-0.txt"),
                "attachments": [],
            }
        ]
    }
    (out / f"{prefix}.manifest.json").write_text(json.dumps(manifest))
    return prefix


@pytest.mark.asyncio
async def test_list_notebooks(report_dirs):
    _out, reps = report_dirs
    (reps / "not-a-report.txt").write_text("x")
    res = await rep_api.list_notebooks(current_user=None)
    names = [n["name"] for n in res["notebooks"]]
    assert names == ["daily_strategy_brief", "linkedin_ads_daily"]


@pytest.mark.asyncio
async def test_list_reports_groups_manifest_reports(report_dirs):
    out, _reps = report_dirs
    _write_run(out)
    res = await rep_api.list_reports(current_user=None, limit=50, notebook=None)
    assert len(res["reports"]) == 1
    r = res["reports"][0]
    assert r["notebook"] == "daily_strategy_brief"
    assert r["subject"] == "Strategy brief"
    assert r["html_file"] == "brief-20261003-103000-0.html"
    assert "brief-20261003-103000-0.html" in r["files"]


@pytest.mark.asyncio
async def test_list_reports_empty_dir(report_dirs):
    res = await rep_api.list_reports(current_user=None, limit=50, notebook=None)
    assert res["reports"] == []


@pytest.mark.asyncio
async def test_list_reports_notebook_filter(report_dirs):
    out, _ = report_dirs
    _write_run(out, name="daily_strategy_brief", run_id="20261001-1")
    _write_run(out, name="linkedin_ads_daily", run_id="20261001-2")
    res = await rep_api.list_reports(current_user=None, limit=50, notebook="linkedin_ads_daily")
    assert all(r["notebook"] == "linkedin_ads_daily" for r in res["reports"])


@pytest.mark.asyncio
async def test_get_report_file_serves_html(report_dirs):
    out, _ = report_dirs
    _write_run(out)
    resp = await rep_api.get_report_file(
        "brief-20261003-103000-0.html", current_user=None
    )
    assert resp.media_type == "text/html"


@pytest.mark.asyncio
async def test_get_report_file_rejects_traversal(report_dirs):
    _out, _reps = report_dirs
    with pytest.raises(HTTPException) as exc:
        await rep_api.get_report_file("../../../etc/passwd", current_user=None)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await rep_api.get_report_file("..%2fmanifest.json", current_user=None)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_get_report_file_missing(report_dirs):
    with pytest.raises(HTTPException) as exc:
        await rep_api.get_report_file("nope.html", current_user=None)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_run_report_enqueues_celery(report_dirs, monkeypatch):
    _out, reps = report_dirs
    (reps / "daily_strategy_brief.ipynb").write_text("{}")
    sent = {}

    class _Res:
        id = "task-123"

    monkeypatch.setattr(
        rep_api.celery_app, "send_task",
        lambda name, kwargs=None: sent.update(name=name, kwargs=kwargs) or _Res(),
    )
    res = await rep_api.run_report(
        rep_api.RunReportRequest(
            notebook="daily_strategy_brief",
            parameters={"insight_days": 7},
            send=True,
        ),
        current_user=None,
    )
    assert res["queued"] is True
    assert sent["name"] == "app.worker.tasks.notebook_reports.run_notebook_report"
    # UI runs disable the email-digest code fallback so errors surface.
    assert sent["kwargs"]["fallback_to_code"] is False
    assert sent["kwargs"]["send"] is True


@pytest.mark.asyncio
async def test_run_report_rejects_unknown_notebook(report_dirs):
    with pytest.raises(HTTPException) as exc:
        await rep_api.run_report(
            rep_api.RunReportRequest(notebook="evil_script"), current_user=None
        )
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException):
        await rep_api.run_report(
            rep_api.RunReportRequest(notebook="../etc/cron.d/x"), current_user=None
        )
