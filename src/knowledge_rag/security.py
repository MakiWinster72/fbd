from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken


_SCRYPT_N = 2**14


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("密码至少需要 10 个字符")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=8, p=1, dklen=32)
    return f"scrypt${_SCRYPT_N}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_value, salt_value, digest_value = encoded.split("$", 3)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_value)
        expected = base64.urlsafe_b64decode(digest_value)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=int(n_value), r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SecretCipher:
    """Encrypt model credentials with a deployment-only master secret."""

    def __init__(self, master_secret: str) -> None:
        self._fernet: Fernet | None = None
        if master_secret:
            key = base64.urlsafe_b64encode(hashlib.sha256(master_secret.encode()).digest())
            self._fernet = Fernet(key)

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        if self._fernet is None:
            raise ValueError("保存 API Key 前必须配置 RAG_MODEL_SECRET_KEY")
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if self._fernet is None:
            raise ValueError("无法读取 API Key：未配置 RAG_MODEL_SECRET_KEY")
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("API Key 解密失败，请检查 RAG_MODEL_SECRET_KEY") from exc
