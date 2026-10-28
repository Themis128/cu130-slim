"""HTTP Message Signatures (RFC 9421) — signature_headers()."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.utils import http_signatures


@pytest.fixture()
def signing_key(monkeypatch):
    sk = Ed25519PrivateKey.generate()
    raw = sk.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    monkeypatch.setenv(
        "BOT_AUTH_SIGNING_KEY_B64",
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
    )
    monkeypatch.setattr(http_signatures, "_key", None)
    monkeypatch.setattr(http_signatures, "_key_kid", None)
    return sk


def test_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("BOT_AUTH_SIGNING_KEY_B64", raising=False)
    monkeypatch.setattr(http_signatures, "_key", None)
    monkeypatch.setattr(http_signatures, "_key_kid", None)
    assert http_signatures.signature_headers("GET", "https://example.com/x") == {}


def test_signed_headers_verify(signing_key):
    headers = http_signatures.signature_headers("GET", "https://example.com/feed?x=1")
    assert set(headers) == {"Signature-Agent", "Signature-Input", "Signature"}
    assert headers["Signature-Agent"].strip('"').endswith("/.well-known/http-message-signatures-directory")
    params = headers["Signature-Input"][len("sig1=") :]
    assert '"@method"' in params and '"@authority"' in params
    assert 'alg="ed25519"' in params and "created=" in params

    # Rebuild the signature base and verify with the public key.
    kid = params.split('keyid="')[1].split('"')[0]
    base = "\n".join(
        [
            '"@method": GET',
            '"@authority": example.com',
            '"@path": /feed?x=1',
            '"signature-agent": https://cloudless.gr/.well-known/http-message-signatures-directory',
            f'"@signature-params": {params}',
        ]
    )
    sig_b64 = headers["Signature"].split("sig1=:")[1].rstrip(":")
    signing_key.public_key().verify(base64.b64decode(sig_b64), base.encode())  # raises on failure

    # kid must be the RFC 7638 thumbprint of the public key.
    pub = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    import hashlib

    x = base64.urlsafe_b64encode(pub).rstrip(b"=").decode()
    thumb = json.dumps(
        {"crv": "Ed25519", "kty": "OKP", "x": x},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    expected = base64.urlsafe_b64encode(hashlib.sha256(thumb).digest()).rstrip(b"=").decode()
    assert kid == expected


def test_invalid_key_returns_empty(monkeypatch):
    monkeypatch.setenv("BOT_AUTH_SIGNING_KEY_B64", "not-a-key!!!")
    monkeypatch.setattr(http_signatures, "_key", None)
    monkeypatch.setattr(http_signatures, "_key_kid", None)
    assert http_signatures.signature_headers("GET", "https://a.b/") == {}
