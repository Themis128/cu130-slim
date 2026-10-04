"""Report endpoints — browse, view, and run notebook-generated reports.

Report notebooks live in ``/notebooks/reports/*.ipynb`` and are executed by
the ``run_notebook_report`` Celery task (papermill on the default queue).
Each run writes ``<name>-<run_id>.{ipynb,manifest.json}`` plus rendered
deliverables (html/txt/png) into ``/notebooks/output``. These endpoints let
the UI list available notebooks, browse prior runs, view rendered output,
and trigger a run without waiting for the scheduled brief.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.models.user import User
from app.services.notebook_runner import OUTPUT_DIR, REPORTS_DIR
from app.worker.celery_app import celery_app

router = APIRouter()

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_MIME = {
    ".html": "text/html",
    ".txt": "text/plain",
    ".png": "image/png",
    ".ipynb": "application/x-ipynb+json",
    ".json": "application/json",
    ".md": "text/markdown",
}


class RunReportRequest(BaseModel):
    notebook: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    # send=False renders output only — no email delivery (UI preview runs).
    send: bool = False


def _notebook_names() -> list[str]:
    if not REPORTS_DIR.is_dir():
        return []
    return sorted(p.stem for p in REPORTS_DIR.glob("*.ipynb"))


def _valid_notebook(name: str) -> str:
    if not _SAFE_NAME.match(name) or name not in _notebook_names():
        raise HTTPException(status_code=404, detail=f"unknown report notebook: {name}")
    return name


def _output_file(filename: str):
    """Resolve ``filename`` inside OUTPUT_DIR — no traversal, no subdirs."""
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    safe = os.path.basename(filename)  # belt-and-braces: strip dir components
    base = str(OUTPUT_DIR.resolve()) + os.sep
    path = os.path.normpath(os.path.join(base, safe))
    if not path.startswith(base):
        raise HTTPException(status_code=400, detail="invalid filename")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="report file not found")
    return Path(path)


@router.get("/notebooks")
async def list_notebooks(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List report notebooks available for on-demand runs."""
    _ = current_user
    notebooks = []
    for name in _notebook_names():
        stat = (REPORTS_DIR / f"{name}.ipynb").stat()
        notebooks.append(
            {
                "name": name,
                "size_bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )
    return {"notebooks": notebooks}


@router.get("")
async def list_reports(
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
    notebook: str | None = Query(default=None),
) -> dict[str, Any]:
    """List generated report runs, newest first.

    Runs are discovered from ``*.manifest.json`` files; output files sharing
    the run's ``<name>-<run_id>`` prefix are attached so the UI can link them.
    """
    _ = current_user
    if not OUTPUT_DIR.is_dir():
        return {"reports": []}

    prefix = f"{notebook}-" if notebook and _SAFE_NAME.match(notebook) else ""
    known = _notebook_names()
    reports_out: list[dict[str, Any]] = []
    for manifest_path in sorted(
        OUTPUT_DIR.glob(f"{prefix}*.manifest.json"), reverse=True
    )[:limit]:
        manifest: dict[str, Any] = {}
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        run_prefix = manifest_path.name[: -len(".manifest.json")]
        # Notebook name = longest known notebook prefix of the run id.
        nb = next(
            (n for n in sorted(known, key=len, reverse=True)
             if run_prefix == n or run_prefix.startswith(n + "-")),
            run_prefix,
        )
        created = datetime.fromtimestamp(
            manifest_path.stat().st_mtime, tz=UTC
        ).isoformat()
        # Flat manifest or multi-report {"reports": […]} — one row each.
        entries = manifest.get("reports") or ([manifest] if manifest else [])
        for rep in entries:
            files = [manifest_path.name, f"{run_prefix}.ipynb"]
            for key in ("html_file", "text_file"):
                if rep.get(key):
                    files.append(Path(rep[key]).name)
            files += [
                Path(a["file"]).name
                for a in rep.get("attachments") or []
                if a.get("file")
            ]
            # Only advertise files that actually exist on disk.
            files = [f for f in dict.fromkeys(files) if (OUTPUT_DIR / f).is_file()]
            reports_out.append(
                {
                    "run": run_prefix,
                    "notebook": nb,
                    "subject": rep.get("subject"),
                    "html_file": (
                        Path(rep["html_file"]).name if rep.get("html_file") else None
                    ),
                    "files": files,
                    "created": created,
                }
            )
    return {"reports": reports_out}


@router.get("/files/{filename}")
async def get_report_file(
    filename: str,
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Serve a rendered report file (html/txt/png/ipynb) from the output dir."""
    _ = current_user
    path = _output_file(filename)
    media_type = _MIME.get(path.suffix.lower())
    # HTML reports render in the UI iframe; mark as inline rather than a download.
    return FileResponse(
        path,
        media_type=media_type,
        filename=None if path.suffix.lower() == ".html" else path.name,
    )


@router.post("/run")
async def run_report(
    body: RunReportRequest,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Queue a report notebook run on the default Celery queue.

    ``send=False`` (default) renders output only — nothing is emailed, so
    this is safe to use for ad-hoc report generation from the UI.
    """
    _ = current_user
    _valid_notebook(body.notebook)
    task = celery_app.send_task(
        "app.worker.tasks.notebook_reports.run_notebook_report",
        kwargs={
            "notebook": body.notebook,
            "parameters": body.parameters,
            "send": body.send,
            # UI-triggered runs must surface notebook errors, not silently
            # fall back to the code-path brief meant for the scheduled job.
            "fallback_to_code": False,
        },
    )
    return {"queued": True, "task_id": task.id, "notebook": body.notebook}
