import base64
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# argon2 is the primary scheme; sha256_crypt kept deprecated so existing
# password hashes still verify and are silently upgraded on next login.
pwd_context = CryptContext(
    schemes=["argon2", "sha256_crypt"],
    deprecated=["sha256_crypt"],
)

_fernet: Fernet | None = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        raw = settings.ENCRYPTION_KEY.encode()
        if len(raw) != 32:
            raise ValueError(
                f"ENCRYPTION_KEY must be exactly 32 bytes; got {len(raw)}. "
                "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(16))\""
            )
        _fernet = Fernet(base64.urlsafe_b64encode(raw))
    return _fernet


def encrypt_token(token: str) -> bytes:
    f = get_fernet()
    return f.encrypt(token.encode())


def decrypt_token(encrypted: bytes) -> str:
    f = get_fernet()
    return f.decrypt(encrypted).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except InvalidTokenError:
        return None


def create_reset_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(hours=1))
    to_encode.update({"exp": expire, "type": "reset"})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
