"""Typed WhatsApp Cloud API client (Meta Graph API).

This is a Python-first port of the design patterns used by the maintained
TypeScript SDK `meta-cloud-api` (froggy1014/meta-cloud-api):
- normalized Meta error envelope parsing
- typed error hierarchy (auth/throttling/send/registration)
- rate-limit awareness via response headers

It intentionally does **not** implement automatic retries to avoid silent retry
storms during phone verification and throttling scenarios; callers can decide
when/how to retry based on the surfaced error metadata.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, TypedDict

import httpx

from app.services.facebook_api import _sanitize_log_text
from app.services.meta_graph import FACEBOOK_GRAPH_VERSION, facebook_graph_url

logger = logging.getLogger(__name__)


class MetaErrorData(TypedDict, total=False):
    message: str
    type: str
    code: int
    error_subcode: int
    fbtrace_id: str
    is_transient: bool
    error_user_title: str
    error_user_msg: str
    error_data: dict[str, Any]


class MetaErrorEnvelope(TypedDict, total=False):
    error: MetaErrorData


@dataclass(frozen=True)
class RateLimitInfo:
    retry_after_s: int | None
    x_app_usage: dict[str, Any] | None
    x_business_use_case_usage: dict[str, Any] | None
    raw_headers: dict[str, str]


def _parse_json_header(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _parse_rate_limit(resp: httpx.Response) -> RateLimitInfo:
    headers = {k.lower(): v for k, v in (resp.headers or {}).items()}
    retry_after_s: int | None = None
    ra = headers.get("retry-after")
    if ra:
        try:
            retry_after_s = int(str(ra).strip())
        except Exception:
            retry_after_s = None

    return RateLimitInfo(
        retry_after_s=retry_after_s,
        x_app_usage=_parse_json_header(headers.get("x-app-usage")),
        x_business_use_case_usage=_parse_json_header(headers.get("x-business-use-case-usage")),
        raw_headers={k: v for k, v in headers.items()},
    )


def _extract_meta_error(resp: httpx.Response) -> MetaErrorData | None:
    try:
        body = resp.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None
    # Keep only keys we care about. TypedDict requires literal keys (no dynamic indexing).
    data: MetaErrorData = {}

    message = err.get("message")
    if isinstance(message, str):
        data["message"] = message

    typ = err.get("type")
    if isinstance(typ, str):
        data["type"] = typ

    code = err.get("code")
    if isinstance(code, int):
        data["code"] = code

    error_subcode = err.get("error_subcode")
    if isinstance(error_subcode, int):
        data["error_subcode"] = error_subcode

    fbtrace_id = err.get("fbtrace_id")
    if isinstance(fbtrace_id, str):
        data["fbtrace_id"] = fbtrace_id

    is_transient = err.get("is_transient")
    if isinstance(is_transient, bool):
        data["is_transient"] = is_transient

    error_user_title = err.get("error_user_title")
    if isinstance(error_user_title, str):
        data["error_user_title"] = error_user_title

    error_user_msg = err.get("error_user_msg")
    if isinstance(error_user_msg, str):
        data["error_user_msg"] = error_user_msg

    error_data = err.get("error_data")
    if isinstance(error_data, dict):
        data["error_data"] = error_data

    return data or None


def _human_error_message(err: MetaErrorData | None, fallback: str) -> str:
    """Prefer WhatsApp's real details field when present."""
    if not err:
        return fallback
    error_data = err.get("error_data") or {}
    if isinstance(error_data, dict):
        details = error_data.get("details")
        if isinstance(details, str) and details.strip():
            # Some details strings include newlines/markup; sanitize for logs/exceptions.
            return details.strip()
    user_title = err.get("error_user_title")
    user_msg = err.get("error_user_msg")
    if isinstance(user_title, str) and isinstance(user_msg, str) and (user_title.strip() or user_msg.strip()):
        return f"{user_title.strip()}: {user_msg.strip()}".strip(": ").strip()
    msg = err.get("message")
    if isinstance(msg, str) and msg.strip():
        return msg.strip()
    return fallback


class WhatsAppError(Exception):
    """Base error for WhatsApp Cloud API integration."""


class WhatsAppNetworkError(WhatsAppError):
    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


class WhatsAppApiError(WhatsAppError):
    def __init__(
        self,
        *,
        status_code: int,
        url: str,
        response_text: str,
        error: MetaErrorData | None,
        rate_limit: RateLimitInfo,
    ):
        self.status_code = status_code
        self.url = url
        self.response_text = response_text
        self.error = error
        self.rate_limit = rate_limit

        code = self.code
        fbtrace = self.fbtrace_id
        msg = _human_error_message(error, response_text)
        prefix = f"WhatsApp Cloud API error {status_code}"
        if code is not None:
            prefix += f" [code {code}]"
        if fbtrace:
            prefix += f" [fbtrace_id {fbtrace}]"
        super().__init__(f"{prefix}: {msg[:400]}")

    @property
    def code(self) -> int | None:
        if self.error and isinstance(self.error.get("code"), int):
            return int(self.error["code"])
        return None

    @property
    def error_subcode(self) -> int | None:
        if self.error and isinstance(self.error.get("error_subcode"), int):
            return int(self.error["error_subcode"])
        return None

    @property
    def fbtrace_id(self) -> str:
        if self.error and isinstance(self.error.get("fbtrace_id"), str):
            return str(self.error["fbtrace_id"])
        return ""

    @property
    def is_transient(self) -> bool | None:
        if self.error and isinstance(self.error.get("is_transient"), bool):
            return bool(self.error["is_transient"])
        return None


class WhatsAppAuthorizationError(WhatsAppApiError):
    """OAuth / permission errors (e.g. expired token)."""


class WhatsAppThrottlingError(WhatsAppApiError):
    """Rate limiting / throttling errors."""


class WhatsAppSendMessageError(WhatsAppApiError):
    """Message send errors (invalid recipient, window, etc.)."""


class WhatsAppPhoneVerificationError(WhatsAppApiError):
    """Phone verification / registration flow errors."""


_AUTHORIZATION_ERROR_CODES = frozenset({0, 3, 10, 190})
_THROTTLING_ERROR_CODES = frozenset({4, 80007, 130429, 131048, 131056})
_SEND_MESSAGE_ERROR_CODES = frozenset(
    {
        130472,
        131000,
        131005,
        131008,
        131009,
        131016,
        131021,
        131026,
        131030,
        131042,
        131044,
        131045,
        131047,
        131050,
        131051,
        131052,
        131053,
        131056,
        131057,
        131061,
        131062,
        132000,
        132001,
        132005,
        132007,
        132008,
        132012,
        132015,
        132016,
        132068,
        132069,
        135000,
        137000,
    }
)


def _classify_error(
    *,
    status_code: int,
    url: str,
    response_text: str,
    error: MetaErrorData | None,
    rate_limit: RateLimitInfo,
) -> WhatsAppApiError:
    code = None
    if error and isinstance(error.get("code"), int):
        code = int(error["code"])

    # WhatsApp phone verification specific: Meta docs call out 136024 for request_code when already verified.
    if code == 136024:
        return WhatsAppPhoneVerificationError(
            status_code=status_code,
            url=url,
            response_text=response_text,
            error=error,
            rate_limit=rate_limit,
        )

    if code in _AUTHORIZATION_ERROR_CODES or (isinstance(code, int) and 200 <= code < 300):
        return WhatsAppAuthorizationError(
            status_code=status_code,
            url=url,
            response_text=response_text,
            error=error,
            rate_limit=rate_limit,
        )
    if code in _THROTTLING_ERROR_CODES:
        return WhatsAppThrottlingError(
            status_code=status_code,
            url=url,
            response_text=response_text,
            error=error,
            rate_limit=rate_limit,
        )
    if code in _SEND_MESSAGE_ERROR_CODES:
        return WhatsAppSendMessageError(
            status_code=status_code,
            url=url,
            response_text=response_text,
            error=error,
            rate_limit=rate_limit,
        )

    return WhatsAppApiError(
        status_code=status_code,
        url=url,
        response_text=response_text,
        error=error,
        rate_limit=rate_limit,
    )


def _map_upstream_status(status_code: int) -> int:
    """Normalize upstream 5xx to 502/503 for the FastAPI layer."""
    if status_code >= 500:
        return 503 if status_code in (503, 504) else 502
    return status_code


class WhatsAppCloudClient:
    """Low-level WhatsApp Cloud API client (Graph host, versioned)."""

    def __init__(
        self,
        *,
        access_token: str,
        api_version: str = FACEBOOK_GRAPH_VERSION,
        timeout_s: float = 60.0,
    ):
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token
        self.api_version = (api_version or FACEBOOK_GRAPH_VERSION).lstrip("/")
        self.timeout_s = float(timeout_s)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        absolute_url: str | None = None,
    ) -> dict[str, Any]:
        url = absolute_url or facebook_graph_url(path, version=self.api_version)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=self._headers(headers),
                )
        except Exception as e:
            raise WhatsAppNetworkError(f"WhatsApp Cloud API network error for {url}", cause=e) from e

        if resp.status_code < 400:
            try:
                return resp.json()
            except Exception as e:
                raise WhatsAppApiError(
                    status_code=_map_upstream_status(resp.status_code),
                    url=url,
                    response_text=_sanitize_log_text(resp.text),
                    error=None,
                    rate_limit=_parse_rate_limit(resp),
                ) from e

        mapped = _map_upstream_status(resp.status_code)
        err = _extract_meta_error(resp)
        rate = _parse_rate_limit(resp)
        safe_url = _sanitize_log_text(url)
        safe_text = _sanitize_log_text(resp.text)
        logger.error("WhatsApp Cloud API error %s for %s: %s", mapped, safe_url, safe_text)
        raise _classify_error(
            status_code=mapped,
            url=url,
            response_text=resp.text,
            error=err,
            rate_limit=rate,
        )

    async def get_bytes(
        self,
        *,
        absolute_url: str,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        """Fetch raw bytes from a URL that requires the same Bearer token."""
        url = absolute_url
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                resp = await client.get(url, headers=self._headers(headers))
        except Exception as e:
            raise WhatsAppNetworkError(f"WhatsApp Cloud API network error for {url}", cause=e) from e

        if resp.status_code < 400:
            return resp.content

        mapped = _map_upstream_status(resp.status_code)
        err = _extract_meta_error(resp)
        rate = _parse_rate_limit(resp)
        safe_url = _sanitize_log_text(url)
        safe_text = _sanitize_log_text(resp.text)
        logger.error("WhatsApp Cloud API error %s for %s: %s", mapped, safe_url, safe_text)
        raise _classify_error(
            status_code=mapped,
            url=url,
            response_text=resp.text,
            error=err,
            rate_limit=rate,
        )


# Phone numbers are E.164 format: +<country_code><number> (API expects without '+')
_PHONE_RE = re.compile(r"^\+?[0-9]{1,15}$")


def validate_wa_phone(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("Phone number is empty")
    if not _PHONE_RE.match(value):
        raise ValueError(f"Invalid phone number format: {value[:40]}")
    return value.lstrip("+")

