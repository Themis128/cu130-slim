"""Celery tasks — the autonomous-agency pipeline sweeps.

Front Desk (triage) → Architect (site audit) → Sales drafter (proposal,
human-gated) → Supervisor (14-day PM loop). All governance lives in
``scripts/workforce_audit.py`` contracts.
"""

import logging

from celery import shared_task

from app.services import agency_pipeline
from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)


@shared_task
def run_agency_triage_sweep(dry_run: bool = False) -> dict | None:
    """Classify untriaged leads into the service catalog; queue site audits."""
    from app.core.config import get_settings

    if not get_settings().AGENCY_ENABLED:
        return None
    try:
        return run_async(agency_pipeline.triage_sweep())
    except Exception as exc:  # noqa: BLE001
        logger.warning("agency triage sweep failed: %s", exc)
        return None


@shared_task
def run_agency_audit_sweep() -> dict | None:
    """Run pending agency site audits (bounded per run)."""
    from app.core.config import get_settings

    if not get_settings().AGENCY_ENABLED:
        return None
    try:
        return run_async(agency_pipeline.audit_sweep())
    except Exception as exc:  # noqa: BLE001
        logger.warning("agency audit sweep failed: %s", exc)
        return None


@shared_task
def run_agency_proposal_sweep() -> dict | None:
    """Draft proposals for completed audits; posts to the Slack review lane."""
    from app.core.config import get_settings

    if not get_settings().AGENCY_ENABLED:
        return None
    try:
        return run_async(agency_pipeline.proposal_sweep())
    except Exception as exc:  # noqa: BLE001
        logger.warning("agency proposal sweep failed: %s", exc)
        return None


@shared_task
def run_agency_pm_sweep() -> dict | None:
    """Supervisor: drive active projects through the 14-day milestone plan."""
    from app.core.config import get_settings

    if not get_settings().AGENCY_ENABLED:
        return None
    try:
        return run_async(agency_pipeline.pm_sweep())
    except Exception as exc:  # noqa: BLE001
        logger.warning("agency pm sweep failed: %s", exc)
        return None


@shared_task
def run_agency_engineer_sweep() -> dict | None:
    """Engineers: draft PR manifests for sent proposals (plans only)."""
    from app.core.config import get_settings

    if not get_settings().AGENCY_ENABLED:
        return None
    try:
        return run_async(agency_pipeline.engineer_sweep())
    except Exception as exc:  # noqa: BLE001
        logger.warning("agency engineer sweep failed: %s", exc)
        return None
