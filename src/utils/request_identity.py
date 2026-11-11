"""Identity stamped into every book-bound Grok request and checked on every answer (owner,
2026-11-11).

A book-bound request (events, the per-chapter entity prompts, the optional extractors) carries:

* ``BookID`` - the book's deterministic ULID (``deterministic_ids.book_id``): an answer from another
  book's request is caught;
* ``RequestID`` - a short hash of the request itself (prompt + system prompt + kind, before this
  block is added): an answer to ANOTHER request of the same book is caught too. The BookID alone
  would not have caught 2026-11-11's bug - batch answers stored under each other's keys, all from
  one book.

Grok is asked to echo both as top-level fields of its JSON answer. ``verify`` checks them (on a
live answer and on a cached one alike) and removes them, so callers see the answer unchanged:

* ``ok`` - both match;
* ``mismatch`` - an id differs: the answer belongs to another request - never accepted;
* ``missing`` - not echoed: cannot be proven wrong, but not proven right either;
* ``unverifiable`` - the answer is not a JSON object (an array cannot carry the fields).

Deterministic, so the cache key of a request is the same run to run.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional, Tuple

# Kinds whose answers belong to one book's chapter (not cross-book enrichment like a person's
# biography or a place's coordinates) AND are JSON objects. A kind answered with a JSON array
# (equipment_broad) cannot carry the fields and must stay out: the identity block asks for an
# object, which would break its parser. Only ``GrokClient.extract_json`` stamps + verifies: a kind
# read through ``chat_completion`` (casualties, people_groups) must stay out too, or
# ``cache_key_for`` would name a key its reads never use (TODO: move casualties to extract_json).
BOOK_BOUND = frozenset(
    {
        "events",
        "dates",
        "places",
        "people",
        "peoplegroups",
        "weather",
        "logistics",
        "supplemental",
        "source_section",
    }
)
_MARK = "\n\nANSWER IDENTITY (required):"
_RID = re.compile(r'"RequestID": "([0-9a-f]{16})"')


def is_bound(cache_type: str, book: Optional[str]) -> bool:
    return bool(book) and cache_type in BOOK_BOUND


def request_id(prompt: str, system_prompt: Optional[str], cache_type: str) -> str:
    """The request's own id: a short hash of what is asked (before the identity block)."""
    h = hashlib.sha256(f"{cache_type}\0{system_prompt or ''}\0{prompt}".encode("utf-8"))
    return h.hexdigest()[:16]


def _block(bid: str, rid: str) -> str:
    return (
        f"{_MARK} your JSON answer must be an object and include, at its top level, "
        f'"BookID": "{bid}" and "RequestID": "{rid}" - copied exactly. They identify this '
        f"request; do not use them anywhere else in the answer."
    )


def stamp(
    prompt: str,
    system_prompt: Optional[str],
    cache_type: str,
    book: str,
    basis: Optional[str] = None,
) -> Tuple[str, str, str]:
    """(the prompt with its identity block, BookID, RequestID).

    ``basis`` - a caller's STABLE cache key, when it has one (supplemental): its prompt text
    varies between a batch submit and the retrieve re-run, so the RequestID must come from the
    stable key or the cached answer would never match. Idempotent: a prompt that ENDS with this
    book's well-formed block is returned as is, with the RequestID it carries. Text that merely
    contains the marker (a chapter quoting it) is never taken for a block, nor cut."""
    from src.utils.deterministic_ids import book_id

    bid = book_id(book)
    if _MARK in prompt:
        found = _RID.search(prompt.rsplit(_MARK, 1)[1])
        if found and prompt.endswith(_block(bid, found.group(1))):
            return prompt, bid, found.group(1)
    rid = request_id(basis or prompt, system_prompt, cache_type)
    return prompt + _block(bid, rid), bid, rid


def verify(result: Any, bid: str, rid: str) -> Tuple[Any, str]:
    """(the answer without its identity fields, status) - see the module docstring. A field that
    is present and wrong is a ``mismatch``; one that is absent (none, or only one echoed) is
    ``missing``."""
    if not isinstance(result, dict):
        return result, "unverifiable"
    got_book = result.pop("BookID", None)
    got_request = result.pop("RequestID", None)
    if (got_book is not None and str(got_book) != bid) or (
        got_request is not None and str(got_request) != rid
    ):
        return result, "mismatch"
    if got_book is None or got_request is None:
        return result, "missing"
    return result, "ok"
