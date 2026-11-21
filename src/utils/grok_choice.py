"""One-word Grok verdicts read the same way everywhere (task 1.2f).

Several callers ask Grok a closed question ("Answer with exactly one word: YES, NO or UNKNOWN")
and read the reply themselves. A reply that is none of the choices (prose, an apology, an empty
string) used to be read as one of them (`startswith("NO")` matched "NOT SURE") AND stayed in the
client's cache, so every later run read the same unusable answer. ``ask_choice`` takes the reply's
first word; if it is not a choice, the cached reply is evicted and the question asked once more;
still unusable, it returns None and the caller applies its documented safe default.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

_FIRST_WORD = re.compile(r"[^A-Z]*([A-Z]+)")


def first_word(reply: object) -> str:
    """The reply's first word, upper-case, without quotes or punctuation ('' if none)."""
    m = _FIRST_WORD.match(str(reply or "").upper())
    return m.group(1) if m else ""


def evictor(
    grok_client: Any,
    prompt: str,
    system_prompt: Optional[str] = None,
    cache_type: str = "default",
    temperature: float = 0.1,
) -> Optional[Callable[[], Any]]:
    """A callable that drops the cached answer to exactly this request, or None if the client
    cannot (test doubles)."""
    evict = getattr(grok_client, "evict_completion", None)
    if evict is None:
        return None
    return lambda: evict(prompt, system_prompt, cache_type, temperature)


def can_reask(grok_client: Any) -> bool:
    """False in batch mode: there a re-ask would only queue the request again (and a paid answer
    would be thrown away mid-cycle), so an unusable answer is evicted and asked on the next run.
    """
    return getattr(grok_client, "batch_mode", False) is not True


def ask_choice(
    ask: Callable[[], Any],
    choices: Sequence[str],
    evict: Optional[Callable[[], Any]] = None,
    what: str = "verdict",
    reask: bool = True,
) -> Optional[str]:
    """The choice Grok answered (one of ``choices``), or None if its reply was none of them twice.
    ``ask`` makes the (cached) request; ``evict`` drops its cached reply; ``reask`` False (batch
    mode, ``can_reask``) evicts without asking again now. Errors propagate (the caller's own
    fail-safe applies; an outage stops the run, it is not an Exception; BatchModeCollecting
    reaches the batch collector)."""
    for attempt in (1, 2) if reask else (1,):
        word = first_word(ask())
        if word in choices:
            return word
        logger.warning(
            "OBSERV grok.unusable_verdict what=%s attempt=%d got=%r status=warn - not one of %s; "
            "%s",
            what,
            attempt,
            word[:20],
            "/".join(choices),
            (
                "asked again"
                if attempt == 1 and evict and reask
                else "the caller's safe default applies"
            ),
        )
        if evict is None:
            return None
        evict()
    return None
