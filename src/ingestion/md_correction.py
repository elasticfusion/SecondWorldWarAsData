"""Grok second-pass markdown correction for the convert track.

When the deterministic converter (pandoc) fails to produce usable markdown from a
text-bearing document, don't give up immediately: attempt a Grok recovery pass
over the raw extracted text. If Grok also can't produce usable markdown, the
caller off-ramps to human review (reject_to_review). This is "fail → try an
automated repair → escalate to human", never fabricate.

Scope: this is for text-bearing documents (epub/docx/txt) whose converter
choked, NOT for scanned images (those are Chandra's job) and NOT a general
content rewriter — it only re-formats already-present text into clean markdown.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_MIN_USABLE_CHARS = 40


# --- Observability (fail-safe; never raises into the repair path) ------------
# Canonical Phase-0 OBSERV: one greppable line per correction attempt.
# Anti-swallow rule — a repair that errors or yields no usable markdown is LOUD
# (the caller then escalates to human review); a genuine recovery is ``ok``.
def _observe_mdcorrect(changed: bool, status: str, *, reason: str = "") -> None:
    """Emit ``OBSERV mdcorrect.applied changed=<bool> status=ok|skipped|error``.

    ``ok`` is info; ``skipped`` (nothing to repair) and ``error`` are LOUD.
    Fail-safe: observability never raises into the (best-effort) repair.
    """
    try:
        base = "OBSERV mdcorrect.applied changed=%s status=%s" % (bool(changed), status)
        if reason:
            base += " reason=%s" % (str(reason)[:200],)
        if status == "ok":
            logger.info(base)
        else:
            logger.warning(base)
    except Exception:  # noqa: BLE001 - observability never breaks repair
        pass


_SYSTEM = (
    "You repair malformed document text into clean GitHub-flavored Markdown. You "
    "ONLY reformat text that is already present — preserve wording verbatim, fix "
    "structure (headings, paragraphs, lists, tables), and drop obvious extraction "
    "artifacts (control chars, repeated form-feeds). NEVER invent, summarize, "
    "translate, or add content. If the input has no recoverable prose, return an "
    "empty response."
)


def correct_markdown(
    raw_text: str,
    *,
    grok_client,
    source_hint: str = "",
    min_chars: int = _MIN_USABLE_CHARS,
) -> Optional[str]:
    """Attempt a Grok second-pass repair of ``raw_text`` into clean markdown.

    Returns the corrected markdown on success, or ``None`` if Grok is
    unavailable, errors, or returns nothing usable (caller then escalates to
    human review). Never raises — a correction attempt must not crash the task.
    """
    if not raw_text or len(raw_text.strip()) < min_chars:
        # Nothing meaningful to repair — don't waste a Grok call.
        _observe_mdcorrect(False, "skipped", reason="input below minimum usable chars")
        return None
    try:
        prompt = (
            "Repair the following extracted document text into clean Markdown. "
            "Preserve all wording verbatim; only fix structure and remove "
            f"extraction artifacts.{(' Source: ' + source_hint) if source_hint else ''}"
            "\n\n--- RAW TEXT ---\n" + raw_text
        )
        result = grok_client.chat_completion(
            prompt=prompt, system_prompt=_SYSTEM, temperature=0.0, cache_type="default"
        )
        if result and len(result.strip()) >= min_chars:
            logger.info(
                "MD-correction second pass recovered %d chars from %s",
                len(result.strip()),
                source_hint or "raw text",
            )
            _observe_mdcorrect(True, "ok")
            return result
        logger.warning(
            "MD-correction produced no usable markdown for %s", source_hint or "input"
        )
        _observe_mdcorrect(
            False, "error", reason="no usable markdown produced (escalate to review)"
        )
        return None
    except Exception as e:  # noqa: BLE001 - repair is best-effort; escalate on fail
        logger.warning("MD-correction second pass failed for %s: %s", source_hint, e)
        _observe_mdcorrect(False, "error", reason=str(e))
        return None
