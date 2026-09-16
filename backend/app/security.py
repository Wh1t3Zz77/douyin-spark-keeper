import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from cryptography.fernet import Fernet

from .config import get_settings


settings = get_settings()
password_hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=2)


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def hash_password(value: str) -> str:
    return password_hasher.hash(value)


def verify_password(encoded: str, value: str) -> bool:
    try:
        return password_hasher.verify(encoded, value)
    except Exception:
        return False


def digest(value: str) -> str:
    return hmac.new(settings.app_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def random_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def random_token() -> str:
    return secrets.token_urlsafe(32)


def expires_in(**kwargs) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(**kwargs)


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret.encode()).digest())
    return Fernet(key)


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    return _fernet().decrypt(value.encode()).decode()
