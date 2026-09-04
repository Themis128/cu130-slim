"""Direct instagrapi client for Instagram publishing via private mobile API.

Uses username/password login instead of the official Graph API.  Session is
cached as a JSON settings file on the shared uploads volume so re-login is
only needed when the session expires or is explicitly cleared.

All blocking instagrapi calls are dispatched via asyncio.to_thread so the
async event loop is never blocked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Session file lives on the uploads volume (shared between api and workers).
_SESSION_DIR = Path("/app/uploads/.instagrapi")
_SESSION_TTL = 86400 * 6  # 6 days — refresh well before IG's ~90-day limit


class InstagrapiError(Exception):
    """Raised when an instagrapi operation fails."""


class InstagrapiClient:
    """Async-compatible wrapper around instagrapi.Client.

    Creates and caches one Client per username.  Call ``upload_photo``,
    ``upload_video``, or ``upload_album`` — each method ensures the client is
    logged in before executing.
    """

    def __init__(
        self,
        username: str,
        password: str,
        proxy: str | None = None,
    ) -> None:
        self._username = username.strip()
        self._password = password
        self._proxy = proxy or None
        self._client: Any | None = None
        self._session_file = _SESSION_DIR / f"{self._username}.json"

    # ── Internal sync helpers (called from threads) ───────────────────────

    def _build_client(self) -> Any:
        """Instantiate a fresh instagrapi.Client with optional proxy."""
        from instagrapi import Client

        cl = Client()
        if self._proxy:
            try:
                cl.set_proxy(self._proxy)
            except Exception as exc:
                logger.warning("instagrapi: could not set proxy %s: %s", self._proxy, exc)
        return cl

    def _load_session(self, cl: Any) -> bool:
        """Try to restore a previously saved session. Returns True on success."""
        if not self._session_file.exists():
            return False
        age = time.time() - self._session_file.stat().st_mtime
        if age > _SESSION_TTL:
            self._session_file.unlink(missing_ok=True)
            return False
        try:
            settings = json.loads(self._session_file.read_text())
            cl.set_settings(settings)
            # Re-login to refresh the session cookies without a full password auth.
            cl.login(self._username, self._password)
            logger.info("instagrapi: restored session for %s", self._username)
            return True
        except Exception as exc:
            logger.warning("instagrapi: session restore failed (%s); doing fresh login", exc)
            self._session_file.unlink(missing_ok=True)
            return False

    def _save_session(self, cl: Any) -> None:
        try:
            _SESSION_DIR.mkdir(parents=True, exist_ok=True)
            self._session_file.write_text(json.dumps(cl.get_settings()))
        except Exception as exc:
            logger.warning("instagrapi: could not save session: %s", exc)

    def _ensure_client(self) -> Any:
        """Return a logged-in client; login/restore as needed (sync)."""
        if self._client is not None:
            return self._client

        cl = self._build_client()

        if not self._load_session(cl):
            try:
                cl.login(self._username, self._password)
                logger.info("instagrapi: fresh login for %s", self._username)
            except Exception as exc:
                raise InstagrapiError(f"Instagram login failed for {self._username}: {exc}") from exc
            self._save_session(cl)

        self._client = cl
        return cl

    def _invalidate(self) -> None:
        """Discard the cached client and session file (e.g. on session expiry)."""
        self._client = None
        self._session_file.unlink(missing_ok=True)

    def _check_session_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(k in msg for k in ("login_required", "loginrequired", "not authorized", "403"))

    # ── Public async API ──────────────────────────────────────────────────

    async def upload_photo(self, file_path: str, caption: str) -> dict[str, Any]:
        """Upload a single photo post. Returns dict with id, pk, code."""
        def _do() -> dict[str, Any]:
            cl = self._ensure_client()
            try:
                media = cl.photo_upload(Path(file_path), caption)
                return {"id": str(media.id), "pk": str(media.pk), "code": getattr(media, "code", "")}
            except Exception as exc:
                if self._check_session_error(exc):
                    self._invalidate()
                raise InstagrapiError(f"Photo upload failed: {exc}") from exc

        return await asyncio.to_thread(_do)

    async def upload_video(self, file_path: str, caption: str) -> dict[str, Any]:
        """Upload a single video post. Returns dict with id, pk, code."""
        def _do() -> dict[str, Any]:
            cl = self._ensure_client()
            try:
                media = cl.video_upload(Path(file_path), caption)
                return {"id": str(media.id), "pk": str(media.pk), "code": getattr(media, "code", "")}
            except Exception as exc:
                if self._check_session_error(exc):
                    self._invalidate()
                raise InstagrapiError(f"Video upload failed: {exc}") from exc

        return await asyncio.to_thread(_do)

    async def upload_album(self, file_paths: list[str], caption: str) -> dict[str, Any]:
        """Upload a carousel/album post (2-10 mixed photos/videos)."""
        def _do() -> dict[str, Any]:
            cl = self._ensure_client()
            try:
                media = cl.album_upload([Path(fp) for fp in file_paths], caption)
                return {"id": str(media.id), "pk": str(media.pk), "code": getattr(media, "code", "")}
            except Exception as exc:
                if self._check_session_error(exc):
                    self._invalidate()
                raise InstagrapiError(f"Album upload failed: {exc}") from exc

        return await asyncio.to_thread(_do)
