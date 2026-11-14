"""Title uniqueness: titles are the FIRST duplicate indicator.

A title (folder, else filename stem) collision is what triggers a deeper identity
check — not an immediate reject. The identity method matches the submission SHAPE,
decided over the FULL accumulated key set for a title (so concurrent partial
S3->SNS deliveries don't cause false collisions):

  * SINGLE-file submission  -> CHECKSUM identity (exact, cheap, deterministic).
  * MULTI-file submission    -> LLM identity (semantic "same work?" judgment; a
    multi-file set arrives across concurrent partial batches and cannot be
    byte-compared as a set, but an LLM can judge sameness from title + structure
    + sample content).

Outcomes:
  * new title                 -> register, proceed.
  * title exists + SAME item  -> repeat -> REJECT (idempotent; no reprocess).
  * title exists + DIFFERENT   -> collision -> rename (suggest unique title); the
    caller decides how to apply (reject-to-review with the suggestion in dev).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Callable, List, Optional

_LEADING_ARTICLES = ("the", "a", "an")


def normalised_title(title: str) -> str:
    """The form two titles are compared in for duplicates - never the displayed title: accents
    dropped, case folded, punctuation and spacing ignored, a leading article dropped.
    "Cross-Channel_Attack", "cross channel attack" and "The Cross-Channel Attack" are all
    "cross_channel_attack". A match only starts the identity check (checksum / LLM): two
    different works that normalise alike are still told apart and one renamed."""
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    words = re.findall(r"[^\W_]+", text)
    if len(words) > 1 and words[0] in _LEADING_ARTICLES:
        words = words[1:]
    return "_".join(words) or (title or "")


@dataclass
class IdentityDecision:
    action: str  # "new" | "repeat" | "renamed"
    title: str
    method: str = ""  # "checksum" | "llm" | ""
    reason: str = ""


def content_checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _rand_suffix(n: int = 6) -> str:
    alphabet = "abcdefghijkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def decide_identity(
    title: str,
    keys: List[str],
    *,
    lookup: Callable[[str], Optional[str]],
    single_checksum: Callable[[], str],
    multi_same: Callable[[], bool],
    rand: Callable[[], str] = _rand_suffix,
) -> IdentityDecision:
    """Decide the unique title for a submission.

    Args:
        title: derived title (first duplicate indicator).
        keys: the FULL accumulated content keys for this title (len drives method).
        lookup: title -> stored registry marker (checksum for single; "llm" sentinel
            for multi), or None if the title is unused.
        single_checksum: () -> this submission's content checksum (single-file only).
        multi_same: () -> True if the LLM judges this multi-file submission is the
            SAME work as the already-registered title (multi-file only).
        rand: random suffix generator (injectable for tests).
    """
    existing = lookup(title)
    if existing is None:
        method = "checksum" if len(keys) == 1 else "llm"
        return IdentityDecision("new", title, method, "title unused")

    # Title collision -> deeper identity check by submission shape.
    if len(keys) == 1:
        same = single_checksum() == existing
        method = "checksum"
    else:
        same = bool(multi_same())
        method = "llm"

    if same:
        return IdentityDecision("repeat", title, method, "same title + same item")
    for _ in range(5):
        candidate = f"{title}-{rand()}"
        if lookup(candidate) is None:
            return IdentityDecision(
                "renamed", candidate, method, "title collision, different item"
            )
    return IdentityDecision("renamed", f"{title}-{rand()}{rand()}", method, "collision")
