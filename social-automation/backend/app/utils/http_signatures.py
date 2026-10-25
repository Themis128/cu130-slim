"""HTTP Message Signatures (RFC 9421) for outbound bot requests — Web Bot Auth.

Signs requests our agents send to external sites so receivers can verify the
bot's identity against the JWKS published at
``https://cloudless.gr/.well-known/http-message-signatures-directory``.
The private Ed25519 key is provided via ``BOT_AUTH_SIGNING_KEY_B64`` (raw
32-byte key, base64url, no padding). Signing is best-effort: a missing or
invalid key simply yields no headers — the request is never blocked.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_DIRECTORY = "https://cloudless.gr/.well-known/http-message-signatures-directory"
_COVERED = ('"@method"', '"@authority"', '"@path"', '"signature-agent"')

_key = None
_key_kid: str | None = None


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _load_key():
    """Lazily load the Ed25519 private key + derive the JWK thumbprint kid."""
    global _key, _key_kid
    if _key is not None or _key_kid is not None:
        return _key
    raw = os.environ.get("BOT_AUTH_SIGNING_KEY_B64", "").strip()
    if not raw:
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        key = Ed25519PrivateKey.from_private_bytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        )
        pub = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        # RFC 7638 JWK thumbprint — matches the kid served in the JWKS.
        thumb = json.dumps(
            {"crv": "Ed25519", "kty": "OKP", "x": _b64u(pub)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        _key_kid = _b64u(hashlib.sha256(thumb).digest())
        _key = key
    except Exception as exc:  # noqa: BLE001 — never break the caller
        logger.warning("bot-auth signing key unusable: %s", exc)
        _key_kid = ""  # don't retry every call
    return _key


def signature_headers(method: str, url: str) -> dict[str, str]:
    """Return Signature-Input/Signature/Signature-Agent headers for a request.

    ``url`` must be the authority-form URL actually being requested (the
    receiver reconstructs ``@authority``/``@path`` from it). Empty dict when
    signing is not configured.
    """
    key = _load_key()
    if key is None:
        return {}
    try:
        parts = urlsplit(url)
        authority = parts.netloc.lower()
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        created = int(time.time())
        params = (
            f"({ ' '.join(_COVERED) });created={created};"
            f'keyid="{_key_kid}";alg="ed25519"'
        )
        base = "\n".join(
            [
                f'"@method": {method.upper()}',
                f'"@authority": {authority}',
                f'"@path": {path}',
                f'"signature-agent": {_DIRECTORY}',
                f'"@signature-params": {params}',
            ]
        )
        sig = base64.b64encode(key.sign(base.encode())).decode()
        return {
            "Signature-Agent": f'"{_DIRECTORY}"',
            "Signature-Input": f"sig1={params}",
            "Signature": f"sig1=:{sig}:",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot-auth signing failed for %s: %s", url, exc)
        return {}
