"""LinkedIn browser-sidecar re-auth orchestration.

Drives the sidecar's two-phase login (``POST /login/start`` →
``POST /login/verify`` → ``POST /login/cancel``) so a dead LinkedIn
browser session can be recovered through the platform API instead of a
host-side script. When LinkedIn's "quick security check" offers SMS, the
6-digit code is fetched from the Galaxy phone over WiFi ADB using the
pure-python ``adb-shell`` client (no adb binary in the image; the phone's
``~/.android`` keypair is bind-mounted read-only at ``/run/li-adb-keys``).

Credentials come from ``secret_store`` — nothing secret is logged or
returned.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.services.secret_store import secret_store

logger = logging.getLogger(__name__)


class LinkedInReauthError(Exception):
    """Raised when the re-auth flow fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"LinkedIn reauth error {status_code}: {detail}")


class LinkedInReauthClient:
    """HTTP client for the sidecar's two-phase login endpoints."""

    def __init__(self, base_url: str | None = None, timeout: float = 300.0) -> None:
        self.base_url = (
            base_url
            or os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL")
            or "http://linkedin-browser-sidecar:9225"
        )
        self._timeout = timeout

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self._timeout
        ) as c:
            r = await c.post(path, json=payload)
            if r.status_code >= 400:
                raise LinkedInReauthError(r.status_code, r.text)
            return r.json()

    async def start(
        self, username: str, password: str, method: str = "sms"
    ) -> dict[str, Any]:
        return await self._post(
            "/login/start",
            {"username": username, "password": password, "method": method},
        )

    async def verify(self, code: str) -> dict[str, Any]:
        return await self._post("/login/verify", {"code": code})

    async def cancel(self) -> dict[str, Any]:
        return await self._post("/login/cancel", {})

    async def status(self) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=60.0
        ) as c:
            r = await c.get("/session")
            if r.status_code >= 400:
                raise LinkedInReauthError(r.status_code, r.text)
            return r.json()


# ── SMS code fetch (WiFi ADB → content://sms/inbox) ─────────────────────────

_SMS_ADDR_ENV = "LINKEDIN_SMS_ADB_ADDR"
_DEFAULT_SMS_ADDR = "192.168.1.77:5555"
_LI_CODE_RE = re.compile(r"verification code is (\d{6})", re.I)
_LI_HINT_RE = re.compile(r"linkedin", re.I)
_ROW_RE = re.compile(r"Row: \d+ address=([^,]+), date=(\d+), body=(.*)")


def _adb_key_dir() -> Path:
    """Resolve the read-only-mounted ADB key directory.

    The Galaxy phone's WiFi-ADB RSA keypair is bind-mounted into the
    container (compose: ``${LINKEDIN_ADB_KEY_DIR:-~/.android}:
    /run/li-adb-keys:ro``) — adb-shell reads it in place, so the private
    key is never copied or rewritten by this service.
    """
    d = Path(os.environ.get("LINKEDIN_ADB_KEY_DIR", "/run/li-adb-keys"))
    if not (d / "adbkey").exists():
        raise LinkedInReauthError(
            503,
            "ADB key dir missing — mount the phone's ~/.android at "
            "/run/li-adb-keys (or set LINKEDIN_ADB_KEY_DIR); SMS "
            "auto-fetch unavailable, supply the code manually.",
        )
    return d


def _sms_codes_blocking(
    keys_dir: Path, since_ms: int | None
) -> list[tuple[int, str]]:
    """Return [(epoch_ms, code)] for LinkedIn SMS, newest first."""
    from adb_shell.adb_device import AdbDeviceTcp  # lazy: optional dep

    addr = os.environ.get(_SMS_ADDR_ENV, _DEFAULT_SMS_ADDR)
    host, _, port = addr.partition(":")
    device = AdbDeviceTcp(host, int(port or 5555))
    device.connect(rsa_keys=[str(keys_dir / "adbkey")])
    try:
        out = device.shell(
            "content query --uri content://sms/inbox "
            "--projection address,date,body --sort \"date DESC\""
        )
    finally:
        try:
            device.close()
        except Exception:
            pass
    codes: list[tuple[int, str]] = []
    for line in out.splitlines():
        m = _ROW_RE.search(line)
        if not m:
            continue
        _addr, date_s, body = m.groups()
        if not _LI_HINT_RE.search(body):
            continue
        cm = _LI_CODE_RE.search(body)
        if not cm:
            continue
        ts = int(date_s)
        if since_ms is not None and ts <= since_ms:
            continue
        codes.append((ts, cm.group(1)))
    return codes


async def fetch_linkedin_sms_code(
    since_ms: int | None = None, timeout_s: int = 240
) -> str:
    """Poll the phone SMS inbox until a fresh LinkedIn code arrives."""
    import asyncio

    deadline = time.time() + timeout_s
    keys_dir = _adb_key_dir()
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            codes = await asyncio.to_thread(
                _sms_codes_blocking, keys_dir, since_ms
            )
            if codes:
                return codes[0][1]
        except Exception as e:  # device offline, auth, transient
            last_err = e
            logger.warning("LinkedIn SMS poll failed: %s", e)
        await asyncio.sleep(8)
    raise LinkedInReauthError(
        504, f"no LinkedIn SMS code within {timeout_s}s"
        + (f" (last error: {last_err})" if last_err else "")
    )


async def resolve_linkedin_credentials(
    account_id: str | None = None,
) -> tuple[str, str]:
    """Secret-store creds, per-account first then global (profile.py order)."""
    suffix = f"_{account_id}" if account_id else ""
    username = (
        await secret_store.get(f"LINKEDIN_USERNAME{suffix}")
        or await secret_store.get("LINKEDIN_USERNAME")
    )
    password = (
        await secret_store.get(f"LINKEDIN_PASSWORD{suffix}")
        or await secret_store.get("LINKEDIN_PASSWORD")
    )
    if not (username and password):
        raise LinkedInReauthError(
            401,
            "LinkedIn credentials not in secret store "
            "(LINKEDIN_USERNAME / LINKEDIN_PASSWORD or per-account variants).",
        )
    return username, password


async def _arm_sidecar_keepawake(ttl_s: int = 3600) -> None:
    """Best-effort stack-ops keepawake so the sleeper can't stop the
    sidecar mid-login (SMS waits + editor steps can exceed idle_min).
    In-memory on stack-ops — re-armed on every reauth run."""
    base = os.environ.get("STACK_OPS_URL", "http://stack-ops:8787")
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as c:
            await c.post(
                "/keepawake/linkedin-browser-sidecar",
                params={"ttl": ttl_s},
            )
    except Exception as e:
        logger.warning("keepawake arm failed (non-fatal): %s", e)


async def run_reauth(
    username: str,
    password: str,
    method: str = "sms",
    code: str | None = None,
    auto_sms: bool = True,
) -> dict[str, Any]:
    """Full flow: start → (auto-fetch SMS code) → verify → session check."""
    client = LinkedInReauthClient()
    started_ms = int(time.time() * 1000)
    await _arm_sidecar_keepawake()
    res = await client.start(username, password, method=method)

    if res.get("logged_in"):
        return {"logged_in": True, "stage": "done", "detail": res}

    if not res.get("two_factor_required"):
        raise LinkedInReauthError(
            502,
            res.get("message") or res.get("error") or "login start failed",
        )

    if not code and auto_sms:
        code = await fetch_linkedin_sms_code(since_ms=started_ms)
    if not code:
        return {
            "logged_in": False,
            "stage": "enter_code",
            "two_factor_required": True,
            "detail": res,
        }

    verify = await client.verify(code)
    if verify.get("logged_in"):
        return {"logged_in": True, "stage": "done", "detail": verify}
    return {
        "logged_in": False,
        "stage": "enter_code",
        "two_factor_required": True,
        "detail": verify,
    }
