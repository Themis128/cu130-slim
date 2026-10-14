"""Pytest config shared across unit and integration suites.

Some modules initialize Settings at import-time; provide safe test defaults for
required env vars so tests can run without a developer `.env`.
"""

import os

# Many modules import Settings at import-time. Ensure we have a safe, non-default
# JWT secret so tests can collect without requiring a developer .env file.
if os.environ.get("JWT_SECRET_KEY") in (None, "", "change-me-in-production"):
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-" + ("x" * 64)

# Crypto helpers require a 32-byte ENCRYPTION_KEY. Provide a deterministic
# test key when missing/invalid so unit tests can run without a developer .env.
if len((os.environ.get("ENCRYPTION_KEY") or "").encode()) != 32:
    os.environ["ENCRYPTION_KEY"] = "0" * 32

# Redis URL defaults for out-of-container runs (in-container env already sets them).
if not os.environ.get("REDIS_URL"):
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"

if not os.environ.get("MESSENGER_REDIS_URL"):
    os.environ["MESSENGER_REDIS_URL"] = "redis://localhost:6379/1"

# tests/integration/conftest.py loads the repo's real .env into os.environ
# (setdefault) before unit-test modules import Settings. When a developer .env
# carries real INSTAGRAM_* credentials, the module-level _settings in
# app.services.publishing picks them up and unit tests silently take the live
# instagrapi path (real Redis/network). Seed safe-empty defaults here — this
# conftest always loads first — so unit tests stay hermetic unless the
# operator explicitly exports credentials.
for _key in ("INSTAGRAM_USERNAME", "INSTAGRAM_PASSWORD", "INSTAGRAM_PROXY", "INSTAGRAM_PROXY_POOL"):
    os.environ.setdefault(_key, "")
