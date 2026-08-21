import pytest
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    encrypt_token,
    decrypt_token,
)


def test_password_hash_and_verify():
    password = "super-secret-pass"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)


def test_wrong_password_rejected():
    hashed = hash_password("correct")
    assert not verify_password("wrong", hashed)


def test_access_token_roundtrip():
    token = create_access_token({"sub": "user-123", "email": "test@example.com"})
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


def test_refresh_token_roundtrip():
    token = create_refresh_token({"sub": "user-456"})
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "user-456"
    assert payload["type"] == "refresh"


def test_access_token_not_valid_as_refresh():
    token = create_access_token({"sub": "user-123"})
    payload = decode_token(token)
    assert payload["type"] != "refresh"


def test_token_encrypt_decrypt():
    original = "my-oauth-access-token-abc123"
    encrypted = encrypt_token(original)
    assert encrypted != original.encode()
    assert decrypt_token(encrypted) == original


def test_invalid_token_returns_none():
    result = decode_token("not.a.real.token")
    assert result is None
