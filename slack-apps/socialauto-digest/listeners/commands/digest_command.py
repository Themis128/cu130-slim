from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from logging import Logger

from slack_bolt import Ack, Respond


def _social_api_url() -> str:
    return (os.environ.get("SOCIAL_API_URL") or "").rstrip("/")


def _login_and_get_access_token(*, api_url: str, email: str, password: str) -> str:
    login_url = f"{api_url}/api/v1/auth/login"
    body = urllib.parse.urlencode({"username": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        login_url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (internal service)
        data = json.loads(resp.read().decode("utf-8"))
    token = (data or {}).get("access_token") or ""
    if not token:
        raise ValueError("No access_token returned by social-api login")
    return str(token)


def _trigger_digest(*, api_url: str, access_token: str) -> dict:
    # Queue on the worker by default so /digest now responds quickly.
    url = (
        f"{api_url}/api/v1/ops/daily-digest"
        "?days=1&post_to_slack=true&post_to_email=false&async_queue=true"
    )
    req = urllib.request.Request(
        url,
        data=b"",
        headers={"Authorization": f"Bearer {access_token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (internal service)
        return json.loads(resp.read().decode("utf-8"))


def digest_command_callback(command, ack: Ack, respond: Respond, logger: Logger):
    """Slash command: /digest now — trigger the SocialAuto digest immediately."""
    try:
        ack()
        text = (command.get("text") or "").strip().lower()
        if not text or text == "help":
            respond("Usage: `/digest now` — triggers the daily digest to `#socialauto` (queued on workers).")
            return
        if text != "now":
            respond("Unsupported subcommand. Use `/digest now`.")
            return

        api_url = _social_api_url()
        email = os.environ.get("SOCIAL_ADMIN_EMAIL") or ""
        password = os.environ.get("SOCIAL_ADMIN_PASSWORD") or ""
        if not api_url or not email or not password:
            respond("Missing configuration. Set `SOCIAL_API_URL`, `SOCIAL_ADMIN_EMAIL`, and `SOCIAL_ADMIN_PASSWORD`.")
            return

        token = _login_and_get_access_token(api_url=api_url, email=email, password=password)
        result = _trigger_digest(api_url=api_url, access_token=token)
        msg = (result or {}).get("message") or "Digest triggered."
        respond(f"✅ {msg}")
    except Exception:
        logger.exception("Error handling /digest command")
        respond("❌ Failed to trigger digest (see logs).")

