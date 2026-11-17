"""Start-of-name identity gate, shared by every fuzzy deduplicator.

Owner rule (2026-11-06, place dedup): a name is identified by its START. Shared endings are generic
(toponym suffixes "-mouth", "Beach"; citation tails "Army, KTB 1.I.-30.VI.44, 6 Jun 44") and say
nothing about identity, so two names whose first identifying word differs are never duplicates,
however similar their tails: Plymouth / Weymouth, OMAHA Beach / UTAH Beach, and *Seventh Army*
KTB / *Fifteenth Army* KTB (XChannel-8 bibliography: merged at 0.897 SequenceMatcher).
"""

from __future__ import annotations

import os
import re
from typing import AbstractSet

MIN_START_MATCH = 4  # shared leading characters required (fewer if a word is shorter)


def distinctive_start(normalized: str, noise: AbstractSet[str] = frozenset()) -> str:
    """The first identifying word of an already-normalized name: leading ``noise`` words
    ("mont", "saint"; "see", "cf") are skipped while another word follows."""
    tokens = re.findall(r"[a-z0-9]+", normalized)
    while len(tokens) > 1 and tokens[0] in noise:
        tokens.pop(0)
    return tokens[0] if tokens else ""


def starts_match(
    a_normalized: str,
    b_normalized: str,
    noise: AbstractSet[str] = frozenset(),
    min_chars: int = MIN_START_MATCH,
) -> bool:
    """True when two normalized names begin the same way: their first identifying words share
    at least ``min_chars`` leading characters (or all of the shorter word). Tolerates typos in
    the tail of the word ("Marinegruppenkommando" / "Maringruppenkommando")."""
    sa = distinctive_start(a_normalized, noise)
    sb = distinctive_start(b_normalized, noise)
    if not sa or not sb:
        return False
    need = min(min_chars, len(sa), len(sb))
    return len(os.path.commonprefix([sa, sb])) >= need
