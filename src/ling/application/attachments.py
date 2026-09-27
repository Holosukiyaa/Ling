"""Attachment token hashing. Callers keep the raw token out of commands and storage."""

from __future__ import annotations

import hashlib

TOKEN_HASH_LENGTH = 64


def hash_attachment_token(token: str) -> str:
    """Return the lowercase SHA-256 hex digest of a UTF-8 attachment token."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_token_hash(value: object) -> bool:
    """True when `value` is a lowercase SHA-256 hex digest."""

    if not isinstance(value, str) or len(value) != TOKEN_HASH_LENGTH:
        return False
    return all(character in "0123456789abcdef" for character in value)
