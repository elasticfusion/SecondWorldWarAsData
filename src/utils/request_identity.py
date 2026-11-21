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
# biography or a place's coordinates). Each is asked for a JSON object and read through
# ``GrokClient.extract_json``, which stamps + verifies (a kind read through ``chat_completion``
# would have ``cache_key_for`` name a key its reads never use - so none may be). A bare array that
# comes back anyway is ``unverifiable``: accepted, not proven.
BOOK_BOUND = frozenset(
    {
        "events",
        "dates",
        "places",
        "people",
        "peoplegroups",
        "weather",
        "logistics",
        "casualties",
        "people_groups",
        "equipment_broad",
        "equipment_refine",
        "supplemental",
        "source_section",
    }
)
# Phase 3 kinds whose answer is a JSON object about NO single book (a unit, a place, a designation, a
# licence, a search-result verdict, an image verdict): the request's own RequestID is echoed, and
# the BookID too when a book is in scope (``DOC_BOUND`` kinds: the answer is about one document).
# One-word and free-text answers cannot carry an echo: their proof is the id the batch returns
# with each result (``batch_api.align_by_request_id``, strict for Phase 3).
REQUEST_BOUND = frozenset(
    {
        "group_enrichment",
        "place_enrichment",
        "equipment_disambiguation",
        "license_check",
        "openserp_verify",
        "openserp_verify_batch",
        "people_group_source_recheck",
        "equipment_source_recheck",
        "unit_patch_vision",
        "vision_verification",
        "external_maps_verification",
    }
)
DOC_BOUND = frozenset({"in_doc_map_vision"})
_MARK = "\n\nANSWER IDENTITY (required):"
_RID = re.compile(r'"RequestID": "([0-9a-f]{16})"')


def is_bound(cache_type: str, book: Optional[str]) -> bool:
    """A request that carries BookID + RequestID: a chapter kind with a book in scope, or a
    document-bound kind (``DOC_BOUND``) with one."""
    return bool(book) and (cache_type in BOOK_BOUND or cache_type in DOC_BOUND)


def is_request_bound(cache_type: str) -> bool:
    """A request that carries its RequestID (and the BookID too when ``is_bound``)."""
    return cache_type in REQUEST_BOUND or cache_type in DOC_BOUND


def request_id(prompt: str, system_prompt: Optional[str], cache_type: str) -> str:
    """The request's own id: a short hash of what is asked (before the identity block)."""
    h = hashlib.sha256(f"{cache_type}\0{system_prompt or ''}\0{prompt}".encode("utf-8"))
    return h.hexdigest()[:16]


def _block(bid: str, rid: str) -> str:
    if not bid:
        return (
            f"{_MARK} your JSON answer must be an object and include, at its top level, "
            f'"RequestID": "{rid}" - copied exactly. It identifies this request; do not use it '
            f"anywhere else in the answer."
        )
    return (
        f"{_MARK} your JSON answer must be an object and include, at its top level, "
        f'"BookID": "{bid}" and "RequestID": "{rid}" - copied exactly. They identify this '
        f"request; do not use them anywhere else in the answer."
    )


def stamp(
    prompt: str,
    system_prompt: Optional[str],
    cache_type: str,
    book: Optional[str],
    basis: Optional[str] = None,
) -> Tuple[str, str, str]:
    """(the prompt with its identity block, BookID, RequestID). ``book`` None/"": a request-only
    kind: the block carries the RequestID alone and the BookID returned is "".

    ``basis`` - a caller's STABLE cache key, when it has one (supplemental): its prompt text
    varies between a batch submit and the retrieve re-run, so the RequestID must come from the
    stable key or the cached answer would never match. Idempotent: a prompt that ENDS with this
    book's well-formed block is returned as is, with the RequestID it carries. Text that merely
    contains the marker (a chapter quoting it) is never taken for a block, nor cut."""
    from src.utils.deterministic_ids import book_id

    bid = book_id(book) if book else ""  # "" = a request-only kind (no book)
    if _MARK in prompt:
        head, tail = prompt.rsplit(_MARK, 1)
        found = _RID.search(tail)
        # a block is only OURS when its id is the one this request hashes to: text that ends with
        # a well-formed block of its own choosing (a scraped page) is not taken for ours
        if (
            found
            and prompt.endswith(_block(bid, found.group(1)))
            and found.group(1) == request_id(basis or head, system_prompt, cache_type)
        ):
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
    if (bid and got_book is not None and str(got_book) != bid) or (
        got_request is not None and str(got_request) != rid
    ):
        return result, "mismatch"
    if (bid and got_book is None) or got_request is None:
        return result, "missing"
    return result, "ok"
