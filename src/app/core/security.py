"""Encryption utilities.

Authentication moved to Clerk — password hashing and our own JWT mint/verify
were removed in the Clerk cutover. What remains is Fernet encryption for the
user's BYO Google Cloud API key. (The Microsoft-OAuth state token does its own
HS256 signing in app/services/email_microsoft.py.)
"""

import base64

from cryptography.fernet import Fernet

from app.core.config import settings


def get_fernet() -> Fernet:
    """Get Fernet instance for encryption/decryption."""
    key = settings.ENCRYPTION_KEY
    # If key is not a valid Fernet key, derive one deterministically from it.
    if len(key) != 44:  # Fernet keys are 44 chars base64
        import hashlib

        key_bytes = hashlib.sha256(key.encode()).digest()
        key = base64.urlsafe_b64encode(key_bytes)
    else:
        key = key.encode() if isinstance(key, str) else key
    return Fernet(key)


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key for storage."""
    return get_fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt a stored API key."""
    return get_fernet().decrypt(encrypted_key.encode()).decode()
