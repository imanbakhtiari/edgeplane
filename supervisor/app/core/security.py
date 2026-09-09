import base64
import hashlib
import os
import re
from argon2 import PasswordHasher
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.core.settings import settings

passwords = PasswordHasher()


def encrypt(value: str) -> str:
    key = base64.urlsafe_b64decode(settings.master_encryption_key)
    nonce = os.urandom(12)
    return base64.urlsafe_b64encode(nonce + AESGCM(key).encrypt(nonce, value.encode(), b"cdn-v1")).decode()


def decrypt(value: str) -> str:
    blob = base64.urlsafe_b64decode(value)
    return (
        AESGCM(base64.urlsafe_b64decode(settings.master_encryption_key))
        .decrypt(blob[:12], blob[12:], b"cdn-v1")
        .decode()
    )


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def redact(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if any(
                s in k.lower()
                for s in ["password", "secret", "private", "token", "encrypted", "api_key", "csrf"]
            )
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"-----BEGIN .*?-----.*?-----END .*?-----", "[REDACTED PEM]", value, flags=re.S)
    return value
