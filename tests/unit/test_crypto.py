import pytest
from cryptography.fernet import Fernet

from gca.crypto import CryptoError, decrypt_str, encrypt_str


def test_roundtrip(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    token = encrypt_str("github_pat_secret")
    assert token != "github_pat_secret"
    assert decrypt_str(token) == "github_pat_secret"


def test_missing_key(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    # A developer .env in the repo root would provide the key via env_file;
    # run from an empty directory so the key is truly absent.
    monkeypatch.chdir(tmp_path)
    from gca.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(CryptoError, match="APP_SECRET_KEY"):
            encrypt_str("x")
    finally:
        get_settings.cache_clear()


def test_wrong_key(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    token = encrypt_str("value")
    monkeypatch.setenv("APP_SECRET_KEY", Fernet.generate_key().decode())
    from gca.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(CryptoError, match="wrong APP_SECRET_KEY"):
        decrypt_str(token)
