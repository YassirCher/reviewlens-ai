from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError

PASSWORD_HASHER = PasswordHasher(type=Type.ID)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def normalize_admin_identifier(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized or len(normalized) > 320 or "@" not in normalized:
        raise ValueError("A valid admin email is required")
    return normalized


def normalize_user_email(value: str) -> str:
    normalized = value.strip().casefold()
    if not normalized or len(normalized) > 320 or "@" not in normalized or "." not in normalized:
        raise ValueError("A valid email address is required")
    return normalized


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("Admin password must contain at least 8 characters")
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError, VerificationError):
        return False


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def keyed_hash(value: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def secure_hash_matches(value: str, expected_hash: str, secret: str) -> bool:
    return hmac.compare_digest(keyed_hash(value, secret), expected_hash)
