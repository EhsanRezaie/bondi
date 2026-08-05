# app/core/encryption.py
import asyncio
import base64
import functools
import os
from typing import Optional
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("core.encryption")


def _derive_key(match_id: str, secret: str) -> bytes:
    """
    Derive a unique encryption key for a chat from match_id and a secret.

    This is the raw KDF — no caching. Use this directly when you need
    to derive a key with a specific secret (e.g. rotation script).

    Args:
        match_id: The match ID (as string)
        secret:   The encryption secret to use

    Returns:
        32-byte key for AES-256-GCM encryption
    """
    salt = match_id.encode('utf-8')

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )

    return kdf.derive(secret.encode('utf-8'))


@functools.lru_cache(maxsize=4096)
def derive_chat_key(match_id: str) -> bytes:
    """
    Derive a unique encryption key for a chat from match_id and the
    server's current ENCRYPTION_SECRET.

    Result is cached — deterministic for a given match_id, so the
    expensive PBKDF2-100k runs only once per chat per process.

    Args:
        match_id: The match ID (as string)

    Returns:
        32-byte key for AES-256-GCM encryption
    """
    return _derive_key(match_id, settings.ENCRYPTION_SECRET)


def encrypt_message(content: str, match_id: str) -> str:
    """
    Encrypt a message using AES-256-GCM with the current ENCRYPTION_SECRET.

    Args:
        content: Plaintext message to encrypt
        match_id: Match ID for key derivation

    Returns:
        Base64 encoded encrypted string (nonce + ciphertext + tag)
    """
    if not content:
        return content

    key = derive_chat_key(match_id)

    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, content.encode('utf-8'), None)

    combined = nonce + ciphertext
    return base64.b64encode(combined).decode('utf-8')


def decrypt_message(encrypted: str, match_id: str) -> str:
    """
    Decrypt a message using AES-256-GCM with the current ENCRYPTION_SECRET.

    Args:
        encrypted: Base64 encoded encrypted string
        match_id: Match ID for key derivation

    Returns:
        Plaintext message
    """
    if not encrypted:
        return encrypted

    key = derive_chat_key(match_id)

    combined = base64.b64decode(encrypted.encode('utf-8'))
    nonce = combined[:12]
    ciphertext = combined[12:]

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode('utf-8')


def encrypt_with_secret(content: str, match_id: str, secret: str) -> str:
    """
    Encrypt a message using AES-256-GCM with an explicit secret.

    Use this in the rotation script to re-encrypt with a new secret.
    Does not use the LRU cache — safe to call with any secret.

    Args:
        content: Plaintext message to encrypt
        match_id: Match ID for key derivation
        secret:   The encryption secret to use (must be the same length
                  as ENCRYPTION_SECRET)

    Returns:
        Base64 encoded encrypted string (nonce + ciphertext + tag)
    """
    if not content:
        return content

    key = _derive_key(match_id, secret)

    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, content.encode('utf-8'), None)

    combined = nonce + ciphertext
    return base64.b64encode(combined).decode('utf-8')


def decrypt_with_secret(encrypted: str, match_id: str, secret: str) -> str:
    """
    Decrypt a message using AES-256-GCM with an explicit secret.

    Use this in the rotation script to decrypt with the old secret.
    Does not use the LRU cache — safe to call with any secret.

    Args:
        encrypted: Base64 encoded encrypted string
        match_id: Match ID for key derivation
        secret:    The encryption secret to use (the old secret)

    Returns:
        Plaintext message
    """
    if not encrypted:
        return encrypted

    key = _derive_key(match_id, secret)

    combined = base64.b64decode(encrypted.encode('utf-8'))
    nonce = combined[:12]
    ciphertext = combined[12:]

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode('utf-8')


async def decrypt_message_async(encrypted: str, match_id: str) -> str:
    """
    Decrypt a message off the event loop via threadpool.

    Use this in async contexts to avoid blocking the loop on the
    PBKDF2 key derivation (100k iterations) on cold cache miss.
    """
    if not encrypted:
        return encrypted

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, decrypt_message, encrypted, match_id)


def encrypt_content_for_admin(content: str, match_id: str) -> str:
    """
    Alias for encrypt_message - used for admin visibility.
    """
    return encrypt_message(content, match_id)


def decrypt_content_for_admin(encrypted: str, match_id: str) -> str:
    """
    Alias for decrypt_message - used for admin visibility.
    """
    return decrypt_message(encrypted, match_id)