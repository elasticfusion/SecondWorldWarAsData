"""Title uniqueness: titles must be unique across the corpus.

Rule (owner-directed):
  * A title is derived from the content folder, else the filename stem.
  * Same title + SAME content (checksum) -> it's a repeat -> REJECT (no reprocess).
  * Same title + DIFFERENT content -> a genuinely new item that collides -> make
    the title unique by appending a short random suffix (``Title-ab12cd``).
  * New title -> register and proceed.

A small DynamoDB registry (``title#{TITLE}`` -> checksum) backs the check. The
decision logic is pure/testable; the registry read/write is a thin wrapper.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class TitleDecision:
    """Outcome of a uniqueness check."""

    action: str  # "new" | "repeat" | "renamed"
    title: str  # the title to use (possibly suffixed); unchanged for repeat/new
    reason: str = ""


def content_checksum(data: bytes) -> str:
    """Stable content identity for an uploaded item."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _rand_suffix(n: int = 6) -> str:
    # URL/path-safe short suffix; avoid ambiguous chars.
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def decide_title(
    title: str,
    checksum: str,
    lookup: Callable[[str], Optional[str]],
    _rand: Callable[[], str] = _rand_suffix,
) -> TitleDecision:
    """Decide the unique title for an upload.

    Args:
        title: the derived (folder/filename) title.
        checksum: this upload's content checksum.
        lookup: title -> registered checksum (or None if the title is unused).
    """
    existing = lookup(title)
    if existing is None:
        return TitleDecision("new", title, "title unused")
    if existing == checksum:
        return TitleDecision("repeat", title, "same title + identical content")
    # Collision: same title, different content -> make unique. Loop in the
    # (astronomically unlikely) event the suffixed title is itself taken.
    for _ in range(5):
        candidate = f"{title}-{_rand()}"
        if lookup(candidate) is None:
            return TitleDecision(
                "renamed", candidate, "title collision with different content"
            )
    # Last resort: longer suffix.
    return TitleDecision("renamed", f"{title}-{_rand()}{_rand()}", "collision (retry)")
