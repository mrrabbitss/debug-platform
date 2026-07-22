from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretDecryptionError(RuntimeError):
    pass


@lru_cache
def _get_fernet() -> Fernet:
    configured_key = get_settings().model_secret_key.strip()
    if configured_key:
        key = configured_key.encode("ascii")
    else:
        key_path = Path("./data/model_secret.key")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.is_file():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            key_path.write_bytes(key + b"\n")
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise SecretDecryptionError("MODEL_SECRET_KEY is not a valid Fernet key") from exc


def encrypt_secret(value: str) -> str:
    return _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _get_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptionError(
            "The saved API key cannot be decrypted. Restore MODEL_SECRET_KEY or enter the API key again."
        ) from exc


def secret_hint(value: str) -> str:
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"
