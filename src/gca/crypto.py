"""Symmetric encryption for credentials at rest (Fernet, key from APP_SECRET_KEY)."""

from cryptography.fernet import Fernet, InvalidToken

from gca.config import get_settings


class CryptoError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().app_secret_key
    if not key:
        raise CryptoError(
            "APP_SECRET_KEY is not set; cannot encrypt or decrypt credentials"
        )
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise CryptoError("APP_SECRET_KEY is not a valid Fernet key") from exc


def encrypt_str(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_str(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError(
            "cannot decrypt value: wrong APP_SECRET_KEY or corrupted data"
        ) from exc
