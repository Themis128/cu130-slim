#!/usr/bin/env python3
"""Shared helpers for .devin skill scripts.

Replaces the repeated bash boilerplate: load repo .env, log in to the
SocialAuto API, and make authenticated JSON requests — stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def repo_root() -> Path:
    """Repo root from anywhere under .devin/skills/<name>/scripts/."""
    p = Path(__file__).resolve()
    # _lib lives at .devin/skills/_lib → parents[3] is repo root
    return p.parents[3]


def load_env(env_path: Path | None = None) -> dict[str, str]:
    path = env_path or repo_root() / ".env"
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def env(key: str, default: str = "") -> str:
    return os.environ.get(key) or load_env().get(key) or default


def api_base(env_name: str, default: str) -> str:
    return os.environ.get(env_name) or default


def request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    data: dict | str | None = None,
    form: dict | None = None,
    headers: dict | None = None,
    raw: bool = False,
    timeout: int = 60,
):
    h = dict(headers or {})
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif data is not None:
        body = (data if isinstance(data, str) else json.dumps(data)).encode()
        h.setdefault("Content-Type", "application/json")
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            if raw:
                return payload.decode(errors="replace")
            return json.loads(payload) if payload.strip() else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        print(f"{method} {url} -> HTTP {e.code}\n{body_text}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"{method} {url} -> connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


def request_status(
    method: str,
    url: str,
    *,
    token: str | None = None,
    data: dict | str | None = None,
    headers: dict | None = None,
    timeout: int = 60,
) -> tuple[int, str]:
    """Like request() but returns (http_status, raw_body) without exiting."""
    h = dict(headers or {})
    body = None
    if data is not None:
        body = (data if isinstance(data, str) else json.dumps(data)).encode()
        h.setdefault("Content-Type", "application/json")
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        return 0, str(e.reason)


def api_login(api: str, email: str | None = None, password: str | None = None) -> str:
    email = email or env("SOCIAL_ADMIN_EMAIL")
    password = password or env("SOCIAL_ADMIN_PASSWORD")
    resp = request(
        "POST",
        f"{api}/api/v1/auth/login",
        form={"username": email, "password": password},
    )
    return resp["access_token"]


def social_api() -> tuple[str, str]:
    """(base_url, token) for the SocialAuto API."""
    api = api_base("SOCIAL_API_URL", "http://127.0.0.1:8083")
    return api, api_login(api)


def get_secret(key: str) -> str:
    api, token = social_api()
    d = request("GET", f"{api}/api/v1/secrets/{key}", token=token)
    if "detail" in d:
        die(f"Error: {d['detail']}")
    return d.get("value", "")


def set_secret(key: str, value: str, description: str = "") -> None:
    api, token = social_api()
    body: dict = {"value": value}
    if description:
        body["description"] = description
    d = request("POST", f"{api}/api/v1/secrets/{key}", token=token, data=body)
    print(d.get("key", key), d.get("sources", ""))


def upload(
    method: str,
    url: str,
    file_path: str,
    *,
    field: str = "file",
    extra: dict | None = None,
    token: str | None = None,
    timeout: int = 120,
):
    """multipart/form-data file upload (curl -F equivalent)."""
    boundary = "----skillboundary"
    p = Path(file_path)
    parts = []
    for k, v in (extra or {}).items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{p.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode()
        + p.read_bytes()
        + f"\r\n--{boundary}--\r\n".encode()
    )
    req = urllib.request.Request(
        url,
        data=b"".join(parts),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"{method} {url} -> HTTP {e.code}\n{e.read().decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"{method} {url} -> connection failed: {e.reason}", file=sys.stderr)
        sys.exit(1)


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)


def usage(msg: str) -> None:
    die(f"Usage: {msg}", 2)
