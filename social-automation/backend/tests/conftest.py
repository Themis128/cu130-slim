# Root conftest — shared across unit and integration suites.
# DB fixtures live in tests/integration/conftest.py so unit tests
# can run without a live database.

import os


# Many modules import Settings at import-time. Ensure we have a safe, non-default
# JWT secret so tests can collect without requiring a developer .env file.
if os.environ.get("JWT_SECRET_KEY") in (None, "", "change-me-in-production"):
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-" + ("x" * 64)

# Crypto helpers require a 32-byte ENCRYPTION_KEY. Provide a deterministic
# test key when missing/invalid so unit tests can run without a developer .env.
if len((os.environ.get("ENCRYPTION_KEY") or "").encode()) != 32:
    os.environ["ENCRYPTION_KEY"] = "0" * 32
