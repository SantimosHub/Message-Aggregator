"""
Шифрование поля `credentials` перед сохранением в БД (README, раздел 8:
"ключ и вебхук хранятся в БД в зашифрованном виде").

Ключ шифрования (CREDENTIALS_ENCRYPTION_KEY) — только в переменных окружения,
никогда в БД и не в коде. Использует Fernet (симметричное шифрование,
аутентифицированное — подмена шифротекста будет обнаружена при расшифровке).
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings


class CredentialsCryptoError(Exception):
    """Ошибка шифрования/расшифровки credentials — не путать с бизнес-ошибками."""


def generate_encryption_key() -> str:
    """
    Генерирует новый ключ для CREDENTIALS_ENCRYPTION_KEY.
    Использовать один раз при первом деплое, сохранить в .env/секреты сервера,
    никогда не менять после того, как в БД уже есть зашифрованные записи
    (иначе расшифровка старых записей станет невозможна).
    """
    return Fernet.generate_key().decode("ascii")


@lru_cache
def _get_fernet() -> Fernet:
    settings = get_settings()
    key = settings.credentials_encryption_key
    if not key:
        raise CredentialsCryptoError(
            "CREDENTIALS_ENCRYPTION_KEY не задан в окружении. "
            "Сгенерировать: python -c \"from app.crypto import generate_encryption_key; "
            "print(generate_encryption_key())\""
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise CredentialsCryptoError(
            "CREDENTIALS_ENCRYPTION_KEY имеет неверный формат — ожидается "
            "32-байтный ключ в urlsafe-base64 (см. generate_encryption_key())."
        ) from exc


def encrypt_credentials(plaintext: str) -> str:
    """Шифрует значение credentials перед записью в БД. Возвращает строку для хранения."""
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_credentials(ciphertext: str) -> str:
    """Расшифровывает значение credentials, прочитанное из БД."""
    try:
        return _get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialsCryptoError(
            "Не удалось расшифровать credentials — неверный ключ шифрования "
            "или запись повреждена/подделана."
        ) from exc
