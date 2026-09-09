#!/usr/bin/env python3
"""Rewrite Instagram profile helpers so every path returns or raises (no fallthrough)."""
from pathlib import Path

path = Path("/home/tbaltzakis/cu130-slim/social-automation/backend/app/scripts/instagram_profile_update.py")
text = path.read_text()

old = '''def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _post_json(path: str, data: dict) -> dict:
    """POST JSON to the browser bridge and return the response."""
    req = Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        _die(f"ERROR {exc.code}: {body}")
    except URLError as exc:
        _die(f"Connection error: {exc}")


def _patch_json(path: str, data: dict) -> dict:
    """PATCH JSON to the browser bridge and return the response."""
    req = Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        _die(f"ERROR {exc.code}: {body}")
    except URLError as exc:
        _die(f"Connection error: {exc}")


def _get_json(path: str) -> dict:
    """GET JSON from the browser bridge."""
    req = Request(f"{BRIDGE_URL}{path}", method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        _die(f"ERROR {exc.code}: {body}")
    except URLError as exc:
        _die(f"Connection error: {exc}")
'''

new = '''def _post_json(path: str, data: dict) -> dict:
    """POST JSON to the browser bridge and return the response."""
    req = Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        print(f"ERROR {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1) from None
    except URLError as exc:
        print(f"Connection error: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


def _patch_json(path: str, data: dict) -> dict:
    """PATCH JSON to the browser bridge and return the response."""
    req = Request(
        f"{BRIDGE_URL}{path}",
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        print(f"ERROR {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1) from None
    except URLError as exc:
        print(f"Connection error: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


def _get_json(path: str) -> dict:
    """GET JSON from the browser bridge."""
    req = Request(f"{BRIDGE_URL}{path}", method="GET")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        print(f"ERROR {exc.code}: {body}", file=sys.stderr)
        raise SystemExit(1) from None
    except URLError as exc:
        print(f"Connection error: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
'''

if old not in text:
    raise SystemExit('old block not found')
text = text.replace(old, new)
text = text.replace('from typing import NoReturn\n', '')
path.write_text(text)
print('updated')
