"""Media storage integrity checks and bounded self-repair.

``media_assets`` rows are database claims about bytes living in one of three
stores: local uploads, MinIO, or Cloudflare R2. Storage can drift for reasons
outside the ORM's visibility (bucket recreation, failed presigned uploads,
backend fallback). This service turns that drift into explicit, auditable
state instead of letting publishing discover it later.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.path_utils import safe_resolve
from app.models.content import MediaAsset, Post, PostStatus, StorageBackend
from app.models.user import AuditLog
from app.services import minio_storage, r2_presigned, r2_storage

logger = logging.getLogger(__name__)
settings = get_settings()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/uploads")
_CHECK_KEY = "storage_integrity"
_BACKEND_ORDER = (StorageBackend.r2, StorageBackend.minio, StorageBackend.local)


@dataclass
class ObjectProbe:
    backend: StorageBackend
    status: str  # ok | missing | size_mismatch | unavailable
    size: int | None = None
    public_url: str | None = None
    error: str | None = None


def _backend_value(value: Any) -> str:
    if isinstance(value, StorageBackend):
        return value.value
    return str(value or "").lower()


def _public_url_for(backend: StorageBackend, key: str) -> str | None:
    if backend == StorageBackend.r2:
        return r2_presigned._public_url(key)
    base = (settings.MEDIA_PUBLIC_BASE_URL or "").rstrip("/")
    quoted = urllib.parse.quote(key)
    if base:
        return f"{base}/api/v1/media/view?path={quoted}"
    return f"/api/v1/media/view?path={quoted}"


def _local_probe(key: str) -> ObjectProbe:
    try:
        target = safe_resolve(UPLOAD_DIR, key)
        if target.is_file():
            return ObjectProbe(
                backend=StorageBackend.local,
                status="ok",
                size=target.stat().st_size,
                public_url=_public_url_for(StorageBackend.local, key),
            )
        return ObjectProbe(backend=StorageBackend.local, status="missing")
    except ValueError as exc:
        return ObjectProbe(backend=StorageBackend.local, status="missing", error=str(exc))
    except Exception as exc:  # noqa: BLE001 — filesystem availability is runtime data
        return ObjectProbe(backend=StorageBackend.local, status="unavailable", error=str(exc))


async def probe_object(backend: str | StorageBackend, key: str) -> ObjectProbe:
    """Probe one backend without treating a transport failure as deletion."""
    try:
        backend = StorageBackend(_backend_value(backend))
    except ValueError:
        return ObjectProbe(backend=StorageBackend.local, status="missing", error=f"unsupported backend {backend!r}")

    if backend == StorageBackend.local:
        return _local_probe(key)
    try:
        if backend == StorageBackend.r2:
            meta = await r2_storage.head_object(key)
        else:
            meta = await minio_storage.head_object(key)
        return ObjectProbe(
            backend=backend,
            status="ok",
            size=meta.get("size"),
            public_url=_public_url_for(backend, key),
        )
    except HTTPException as exc:
        if exc.status_code in {400, 404}:
            return ObjectProbe(backend=backend, status="missing", error=exc.detail)
        return ObjectProbe(backend=backend, status="unavailable", error=exc.detail)
    except Exception as exc:  # noqa: BLE001 — object-store errors are runtime data
        return ObjectProbe(backend=backend, status="unavailable", error=str(exc))


def _size_matches(expected: int | None, probe: ObjectProbe) -> bool:
    return expected is None or probe.size is None or probe.size == expected


async def verify_uploaded_object(
    key: str,
    *,
    backend: str | None = None,
    expected_size: int | None = None,
) -> ObjectProbe:
    """Locate a completed presigned-upload object before creating its DB row.

    ``backend`` is the backend returned by ``/upload/prepare``. When present,
    the object must exist there. Older clients may omit it, in which case the
    object is found by probing R2, MinIO, then local storage. The returned
    probe is the source of truth — never trust a client-provided public URL.
    """
    if backend:
        try:
            declared = StorageBackend(_backend_value(backend))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Unsupported storage backend: {backend}") from exc
        candidates = [declared]
    else:
        candidates = list(_BACKEND_ORDER)

    unavailable: list[str] = []
    size_mismatch = False
    for candidate in candidates:
        probe = await probe_object(candidate, key)
        if probe.status == "ok":
            if _size_matches(expected_size, probe):
                return probe
            size_mismatch = True
            continue
        if probe.status == "unavailable":
            unavailable.append(candidate.value)

    if unavailable:
        raise HTTPException(
            status_code=502,
            detail=f"Could not verify uploaded object; storage backend unavailable: {', '.join(sorted(set(unavailable)))}",
        )
    if size_mismatch:
        raise HTTPException(status_code=400, detail="Uploaded object size does not match the prepared upload")
    raise HTTPException(status_code=400, detail="Uploaded object was not found in object storage")


async def _find_asset_source(asset: MediaAsset) -> tuple[ObjectProbe | None, ObjectProbe]:
    """Return (safe_source, declared_probe) for one asset."""
    declared_backend = _backend_value(asset.storage_backend)
    declared = await probe_object(declared_backend, asset.storage_path)
    if declared.status == "ok":
        if _size_matches(asset.size_bytes, declared):
            return declared, declared
        declared = ObjectProbe(
            backend=declared.backend,
            status="size_mismatch",
            size=declared.size,
            public_url=declared.public_url,
            error=f"recorded size {asset.size_bytes} != object size {declared.size}",
        )

    unavailable = False
    for backend in _BACKEND_ORDER:
        if backend.value == declared_backend:
            continue
        probe = await probe_object(backend, asset.storage_path)
        if probe.status == "ok" and _size_matches(asset.size_bytes, probe):
            return probe, declared
        if probe.status == "unavailable":
            unavailable = True
    if unavailable:
        return None, ObjectProbe(
            backend=declared.backend,
            status="unavailable",
            error="one or more storage backends unavailable",
        )
    return None, declared


def _integrity_meta(asset: MediaAsset) -> dict:
    meta = dict(asset.meta_data or {})
    integrity = dict(meta.get(_CHECK_KEY) or {})
    return integrity


async def inspect_and_repair_asset(asset: MediaAsset, *, referenced: bool) -> dict:
    """Check one asset and apply only metadata/backend-label repairs.

    Missing objects are quarantined (``is_archived=True``) rather than deleted;
    rows are un-quarantined automatically only when this worker previously
    quarantined them for storage-missing reasons.
    """
    now = datetime.now(UTC).isoformat()
    previous = _integrity_meta(asset)
    source, declared = await _find_asset_source(asset)
    meta = dict(asset.meta_data or {})
    integrity = dict(previous)
    result: dict[str, Any] = {
        "asset_id": str(asset.id),
        "filename": asset.filename,
        "referenced": referenced,
        "status": "ok",
    }

    if source is not None:
        actual_backend = StorageBackend(_backend_value(source.backend))
        repaired = actual_backend != asset.storage_backend or source.public_url != asset.public_url
        if repaired:
            previous_backend = _backend_value(asset.storage_backend)
            asset.storage_backend = actual_backend
            asset.public_url = source.public_url
            result["status"] = "repaired"
            result["from_backend"] = previous_backend
            result["to_backend"] = actual_backend.value

        if integrity.get("auto_archived"):
            asset.is_archived = False
            result["unarchived"] = True

        integrity.update(
            {
                "status": "ok",
                "backend": actual_backend.value,
                "checked_at": now,
                "missing_since": None,
                "missing_checks": 0,
                "alerted_at": None,
                "last_error": None,
                "referenced": referenced,
            }
        )
        if result["status"] == "repaired":
            integrity["last_repair"] = {
                "from_backend": _backend_value(declared.backend),
                "to_backend": actual_backend.value,
                "at": now,
            }
    elif declared.status == "unavailable":
        newly_error = previous.get("status") != "backend_error" or not previous.get("alerted_at")
        integrity.update(
            {
                "status": "backend_error",
                "backend": _backend_value(asset.storage_backend),
                "checked_at": now,
                "last_error": declared.error or "storage backend unavailable",
                "referenced": referenced,
            }
        )
        result["status"] = "backend_error"
        result["newly_backend_error"] = newly_error
        result["error"] = declared.error
    else:
        status = "size_mismatch" if declared.status == "size_mismatch" else "missing"
        first_unresolved = previous.get("status") != status or not previous.get("alerted_at")
        integrity.update(
            {
                "status": status,
                "backend": _backend_value(asset.storage_backend),
                "checked_at": now,
                "missing_since": previous.get("missing_since") or now,
                "missing_checks": int(previous.get("missing_checks") or 0) + 1,
                "last_error": declared.error or "object not found on any storage backend",
                "referenced": referenced,
            }
        )
        if not asset.is_archived:
            asset.is_archived = True
            integrity["auto_archived"] = True
            result["quarantined"] = True
        result["status"] = status
        result["newly_unresolved"] = first_unresolved

    meta[_CHECK_KEY] = integrity
    asset.meta_data = meta
    flag_modified(asset, "meta_data")
    result["integrity"] = integrity
    return result


async def run_integrity_sweep(
    db: AsyncSession,
    team_id,
    *,
    batch_size: int = 1000,
    notify: bool = True,
) -> dict:
    """Audit a bounded slice of one team's media library.

    The oldest-updated assets are checked first, so every run rotates through
    the library while still covering the current table in one pass.
    """
    post_rows = (
        await db.execute(
            select(Post.media_ids, Post.music_asset_id).where(
                Post.team_id == team_id,
                Post.status != PostStatus.ARCHIVED,
            )
        )
    ).all()
    referenced_ids = {mid for media_ids, _music_id in post_rows for mid in (media_ids or [])}
    referenced_ids.update(music_id for _media_ids, music_id in post_rows if music_id)

    assets = (
        await db.execute(
            select(MediaAsset)
            .where(MediaAsset.team_id == team_id)
            .order_by(MediaAsset.updated_at.asc())
            .limit(batch_size)
        )
    ).scalars().all()

    summary: dict[str, Any] = {
        "team_id": str(team_id),
        "checked": 0,
        "ok": 0,
        "repaired": 0,
        "missing": 0,
        "size_mismatch": 0,
        "backend_errors": 0,
        "quarantined": 0,
        "unarchived": 0,
        "newly_unresolved": 0,
        "newly_backend_errors": 0,
        "referenced_unresolved": 0,
        "unresolved_assets": [],
        "repaired_assets": [],
        "backend_error_assets": [],
    }

    for asset in assets:
        result = await inspect_and_repair_asset(asset, referenced=asset.id in referenced_ids)
        summary["checked"] += 1
        status = result["status"]
        if status == "ok":
            summary["ok"] += 1
        elif status == "repaired":
            summary["repaired"] += 1
            if len(summary["repaired_assets"]) < 25:
                summary["repaired_assets"].append(result["asset_id"])
        elif status in {"missing", "size_mismatch"}:
            summary[status] += 1
            summary["quarantined"] += 1 if result.get("quarantined") else 0
            summary["newly_unresolved"] += 1 if result.get("newly_unresolved") else 0
            summary["referenced_unresolved"] += 1 if result["referenced"] else 0
            if len(summary["unresolved_assets"]) < 25:
                summary["unresolved_assets"].append(
                    {
                        "id": result["asset_id"],
                        "filename": asset.filename,
                        "status": status,
                        "referenced": result["referenced"],
                    }
                )
        elif status == "backend_error":
            summary["backend_errors"] += 1
            summary["newly_backend_errors"] += 1 if result.get("newly_backend_error") else 0
            if len(summary["backend_error_assets"]) < 25:
                summary["backend_error_assets"].append({"id": result["asset_id"], "error": result.get("error")})
        summary["unarchived"] += 1 if result.get("unarchived") else 0

    db.add(
        AuditLog(
            team_id=team_id,
            action="media-integrity-sweep",
            resource_type="sweep",
            detail=(
                f"checked={summary['checked']} ok={summary['ok']} "
                f"repaired={summary['repaired']} missing={summary['missing']} "
                f"size_mismatch={summary['size_mismatch']} "
                f"backend_errors={summary['backend_errors']} quarantined={summary['quarantined']}"
            ),
            meta=summary,
        )
    )

    alert_sent = False
    if notify and (summary["newly_unresolved"] or summary["newly_backend_errors"]):
        try:
            from app.services.slack_notifications import post_alert_to_slack

            sample = ", ".join(
                f"{item['id'][:8]}:{item['status']}:{(item.get('filename') or 'unnamed')[:40]}"
                for item in summary["unresolved_assets"][:5]
            )
            alert_sent = await post_alert_to_slack(
                "Media integrity sweep found unresolved storage drift: "
                f"{summary['missing']} missing, {summary['size_mismatch']} size-mismatched "
                f"({summary['referenced_unresolved']} referenced by posts), "
                f"{summary['backend_errors']} backend errors, {summary['repaired']} repaired. "
                f"Unresolved assets were quarantined. {sample}"
            )
        except Exception:  # noqa: BLE001 — alerting must never fail the sweep
            logger.warning("media integrity Slack alert failed", exc_info=True)

    if alert_sent:
        for asset in assets:
            integrity = _integrity_meta(asset)
            if integrity.get("status") in {"missing", "size_mismatch", "backend_error"}:
                integrity["alerted_at"] = datetime.now(UTC).isoformat()
                meta = dict(asset.meta_data or {})
                meta[_CHECK_KEY] = integrity
                asset.meta_data = meta
                flag_modified(asset, "meta_data")

    return summary
