#!/usr/bin/env python3
"""Shared WhatsApp phone helpers for this skill's scripts."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402


def wa_url(account_id: str, path: str = "") -> tuple[str, str]:
    api, token = social_api()
    return f"{api}/api/v1/whatsapp/{account_id}/phone{path}", token


def phone_status(account_id: str) -> dict:
    url, token = wa_url(account_id, "/status")
    return request("GET", url, token=token)


def request_code(account_id: str, method: str = "SMS", language: str = "en_US") -> dict:
    url, token = wa_url(account_id, "/request-code")
    return request("POST", f"{url}?code_method={method}&language={language}", token=token)


def verify_code(account_id: str, code: str) -> dict:
    url, token = wa_url(account_id, "/verify-code")
    return request("POST", f"{url}?code={code}", token=token)


def register_phone(account_id: str, pin: str = "") -> dict:
    url, token = wa_url(account_id, "/register")
    body = {"messaging_product": "whatsapp"}
    if pin:
        body["pin"] = pin
    return request("POST", url, token=token, data=body)


def has_error(d: dict) -> str | None:
    if not isinstance(d, dict):
        return "non-dict response"
    if "error" in d:
        return d["error"].get("message", str(d["error"]))
    detail = d.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    return None
