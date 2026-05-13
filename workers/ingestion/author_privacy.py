"""Author privacy: SHA-256(username + Key-Vault-stored salt).

Required for Reddit API ToS compliance post-2023. Raw usernames must NEVER
land in Blob, Event Hubs payloads, Postgres, or logs. The salt is a Key Vault
secret and is held in memory only for the lifetime of the worker process.
"""
from __future__ import annotations

import hashlib


def hash_author(username: str | None, salt: str) -> str:
    """Return SHA-256(username + salt) as hex.

    For deleted accounts (username is None or '[deleted]'), returns the digest
    of the literal string 'deleted' + salt so downstream dedup still works.
    """
    safe_username = username or "deleted"
    if safe_username == "[deleted]":
        safe_username = "deleted"
    digest = hashlib.sha256()
    digest.update(safe_username.encode("utf-8"))
    digest.update(salt.encode("utf-8"))
    return digest.hexdigest()
