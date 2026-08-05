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


@functools.lru_cache(maxsize=4096)
def derive_chat_key(match_id: str) -> bytes:
    """
    Derive a unique encryption key for a chat from match_id and server secret.

    Result is cached — deterministic for a given match_id, so the expensive
    PBKDF2-100k runs only once per chat per process.

    Args:
        match_id: The match ID (as string)

    Returns:
        32-byte key for AES-256-GCM encryption
    """
    # Combine match_id with server secret
    salt = match_id.encode('utf-8')

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,  # 32 bytes = 256 bits for AES-256
        salt=salt,
        iterations=100000,  # High iteration count for security
    )

    # Use the server secret as the key material
    key = kdf.derive(settings.ENCRYPTION_SECRET.encode('utf-8'))
    return key


def encrypt_message(content: str, match_id: str) -> str:
    """
    Encrypt a message using AES-256-GCM.
    
    Args:
        content: Plaintext message to encrypt
        match_id: Match ID for key derivation
    
    Returns:
        Base64 encoded encrypted string (nonce + ciphertext + tag)
    """
    if not content:
        return content
    
    # Derive key from match_id
    key = derive_chat_key(match_id)
    
    # Generate random nonce (12 bytes for AES-GCM)
    nonce = os.urandom(12)
    
    # Create AES-GCM cipher
    aesgcm = AESGCM(key)
    
    # Encrypt the message
    ciphertext = aesgcm.encrypt(nonce, content.encode('utf-8'), None)
    
    # Combine nonce + ciphertext and encode as base64
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode('utf-8')


def decrypt_message(encrypted: str, match_id: str) -> str:
    """
    Decrypt a message using AES-256-GCM.

    Args:
        encrypted: Base64 encoded encrypted string
        match_id: Match ID for key derivation

    Returns:
        Plaintext message
    """
    if not encrypted:
        return encrypted

    # Derive key from match_id
    key = derive_chat_key(match_id)

    # Decode from base64
    combined = base64.b64decode(encrypted.encode('utf-8'))

    # Extract nonce (first 12 bytes) and ciphertext (rest)
    nonce = combined[:12]
    ciphertext = combined[12:]

    # Create AES-GCM cipher
    aesgcm = AESGCM(key)

    # Decrypt the message
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