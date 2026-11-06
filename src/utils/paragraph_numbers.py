"""Normalise LLM-supplied paragraph numbers to ``List[int]``. One rule for every extractor.

Why: paragraph numbers come back from Grok in several shapes: ``[12, 13]``, ``["12"]``, ``"12"``,
``12`` or ``["Paragraph_12"]``. Each extractor handled this differently. Events silently dropped
non-ints; equipment did no check at all, so in the XChannel-8 dev E2E (2026-11-06) one broad item
with string numbers crashed ``n - 1`` and zeroed the whole event's equipment (36 expected, 0
produced; Phase 2 then halted on the yield guard).

The numbers are provenance (they select the verbatim source paragraphs), so anything that cannot be
read as a paragraph number is DROPPED, never guessed, and the drop is reported once per call with
the offending values so the prompt or model can be fixed.
"""

from __future__ import annotations

import re
from typing import Any, List

# 1-9 ASCII digits only: bounds int() (a 5000-digit string raises ValueError, Python's int-conversion
# limit) and refuses non-ASCII digits (Arabic-Indic, fullwidth), which would be a guess.
_PARA_RE = re.compile(r"^\s*(?:paragraph[_\s-]*)?([0-9]{1,9})\s*$", re.IGNORECASE)


def _one(value: Any) -> int | None:
    if isinstance(value, bool):  # bool is an int subclass; True is not paragraph 1
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    if isinstance(value, str):
        m = _PARA_RE.match(value)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
    return None


def coerce_paragraph_numbers(raw: Any, *, scope: str, subject: str = "") -> List[int]:
    """Return the readable paragraph numbers in ``raw`` as ints, order kept, duplicates removed.

    Accepts a list/tuple or a single scalar; ``None`` or empty yields ``[]`` silently. Unreadable
    entries are dropped and reported with ``OBSERV paragraphs.unreadable status=warn``.
    """
    if raw is None or raw == "":
        return []
    items = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    out: List[int] = []
    seen: set = set()
    bad: List[str] = []
    for item in items:
        n = _one(item)
        if n is None:
            bad.append(repr(item)[:20])
        elif n not in seen:
            seen.add(n)
            out.append(n)
    if bad:
        from src.utils.observe import observe_warn_counted

        observe_warn_counted(
            "paragraphs",
            "unreadable",
            key=(scope,),
            site=scope,
            subject=subject[:60] or None,
            dropped=len(bad),
            kept=len(out),
            sample=",".join(bad[:3]),
            note="LLM returned paragraph numbers that are not numbers; dropped (check the prompt/model "
            "for this site; the provenance window for those paragraphs is missing)",
        )
    return out
