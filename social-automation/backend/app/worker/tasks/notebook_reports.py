"""Notebook-driven report delivery — execute a report notebook via papermill,
then send its rendered output through the existing email channel.

The notebook writes subject/html/text + attachments to a manifest; this task
is the delivery boundary so secrets stay in the worker. If the notebook run
fails, ``fallback_to_code`` re-runs the equivalent code-path report so a
brief is never silently skipped.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.notebook_reports.run_notebook_report")
def run_notebook_report(
    notebook: str,
    parameters: dict[str, Any] | None = None,
    *,
    send: bool = True,
    fallback_to_code: bool = True,
) -> dict[str, Any]:
    """Execute ``reports/<notebook>.ipynb`` and email the rendered report.

    Returns execution stats; on notebook failure falls back to the
    code-path strategy report so the daily brief still goes out.
    """
    from app.services.notebook_runner import run_report_notebook

    try:
        result = run_report_notebook(notebook, parameters or {})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Notebook report %s failed", notebook)
        if not fallback_to_code:
            raise
        from app.worker.tasks.digest import _send_strategy_async

        fallback = asyncio.run(
            _send_strategy_async(insight_days=(parameters or {}).get("insight_days", 30), send=send)
        )
        return {
            "notebook": notebook,
            "executed": False,
            "error": str(exc) or repr(exc),
            "fallback": fallback,
        }

    manifest = result["manifest"]
    base_dir = Path(result["manifest_path"]).parent
    # Manifests may carry one report (flat keys) or several ({"reports":[…]})
    # — one per team.
    reports = manifest.get("reports") or ([manifest] if manifest else [])
    emailed = 0
    email_errors: list[str] = []
    if send:
        for rep in reports:
            ok, err = asyncio.run(_deliver_manifest(rep, base_dir))
            emailed += int(ok)
            if err:
                email_errors.append(err)

    return {
        "notebook": notebook,
        "executed": True,
        "duration_s": result["duration_s"],
        "artifact": result["notebook"],
        "emailed": emailed,
        "email_error": "; ".join(email_errors) or None,
    }


async def _deliver_manifest(
    manifest: dict[str, Any], base_dir: Path
) -> tuple[bool, str | None]:
    """Send the notebook's rendered report through the standard email path."""
    from app.services.email_digest import send_email

    def _resolve(f: str | None) -> Path | None:
        if not f:
            return None
        p = Path(f)
        return p if p.is_absolute() else base_dir / p

    html_file = _resolve(manifest.get("html_file"))
    text_file = _resolve(manifest.get("text_file"))
    if not html_file or not text_file:
        return False, "manifest missing html_file/text_file"
    try:
        attachments = [
            {
                "name": a["file"],
                "data": (
                    Path(a["file"])
                    if Path(a["file"]).is_absolute()
                    else base_dir / a["file"]
                ).read_bytes(),
                "cid": a.get("cid"),
                "mime": a.get("mime"),
            }
            for a in manifest.get("attachments") or []
        ]
        await send_email(
            subject=manifest.get("subject") or "SocialAuto report",
            text_body=Path(text_file).read_text(),
            html_body=Path(html_file).read_text(),
            attachments=attachments or None,
        )
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send notebook report")
        return False, str(exc) or repr(exc)
