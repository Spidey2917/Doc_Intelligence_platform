"""Content hashing and stable artifact identifiers."""

from __future__ import annotations

import hashlib


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(root_source_uri: str, container_path: str) -> str:
    """Deterministic artifact id from where the artifact sits in the corpus.

    Two census runs over the same corpus produce the same ids, which keeps
    JSONL outputs diffable and lets later phases join on them.
    """
    raw = f"{root_source_uri}\x00{container_path}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(raw).hexdigest()[:24]
