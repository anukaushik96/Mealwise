"""AES-256-GCM for Swiggy tokens at rest.

These tokens place real orders and spend real money, so the ciphertext — never the token
— is what lands in Postgres: a read-only leak of the database would otherwise be enough
to order on every connected user's account. GCM rather than CBC so tampering is detected
instead of silently decrypting to garbage.

Format: base64(nonce) . base64(ciphertext-with-tag)
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = ["encrypt_token", "decrypt_token"]

_NONCE_BYTES = 12  # 96-bit nonce, the GCM standard


def _key() -> bytes:
    raw = os.environ.get("TOKEN_ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError(
            f"TOKEN_ENCRYPTION_KEY must decode to 32 bytes, got {len(key)}. Generate with: "
            'python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"'
        )
    return key


def encrypt_token(plaintext: str) -> str:
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)
    return f"{base64.b64encode(nonce).decode()}.{base64.b64encode(sealed).decode()}"


def decrypt_token(payload: str) -> str:
    parts = payload.split(".")
    if len(parts) != 2:
        raise ValueError("malformed encrypted token")
    nonce, sealed = (base64.b64decode(p) for p in parts)
    return AESGCM(_key()).decrypt(nonce, sealed, None).decode()
