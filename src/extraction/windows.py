"""Entity discovery from fixed PARAGRAPH WINDOWS of the parsed chapter - raw source text only.

Why (chapter 8 window test, 2026-11-13, three runs per size): asking per sub-event fed Grok its own
event output - its paragraph grouping (12 sub-events one run, 7 the next) and its summary in front of
the text, from which "Eugen König" was named although the text never does. Three-paragraph windows
of the parsed text found more of every type than per sub-event (people 107/106/101 vs 92/90/86,
units 264/254/250 vs 182/193/190, equipment 239/240/235 vs 202/187/174), with no name absent from
the text, at ~300 requests per chapter for four types (1-paragraph windows: ~970).

A window's request depends on the parsed chapter alone, so it can be asked in the same batch round
as the chapter's events (``prefetch``). Its answer is then attributed to the REAL sub-event holding
the paragraph its quote came from (``regroup``): a mention keeps a real Sub-eventID.

Windows are presented to the extractors as one-"sub-event" views (``window_event``), so the per
sub-event machinery (``batch_parallel._per_sub_event``, ``sub_events.run_all``) runs them unchanged.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.paragraph_numbers import paragraph_key, paragraph_number
from src.utils.quote_match import normalize

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_PARAGRAPHS = 3


def window_size() -> int:
    """Paragraphs per window: env ``WINDOW_PARAGRAPHS``, else ``extraction.window_paragraphs`` of
    the loaded config, else 3. ONE source for every caller: the prefetch and the later pass must
    agree, or every window is asked twice (different ranges, different requests)."""
    raw = os.environ.get("WINDOW_PARAGRAPHS") or _configured_size()
    try:
        return max(1, int(raw)) if raw not in (None, "") else DEFAULT_WINDOW_PARAGRAPHS
    except (TypeError, ValueError):
        logger.warning("OBSERV windows.size_invalid value=%r: using default", raw)
        return DEFAULT_WINDOW_PARAGRAPHS


@functools.lru_cache(maxsize=1)
def _configured_size() -> Any:
    """``extraction.window_paragraphs`` of the loaded config (read once per process)."""
    try:
        from src.utils.config import load_config

        config = load_config()
    except Exception as e:  # noqa: BLE001 - the default then, said out loud
        logger.warning(
            "OBSERV windows.config_unreadable err=%s: window size %d",
            type(e).__name__,
            DEFAULT_WINDOW_PARAGRAPHS,
        )
        return None
    return ((config or {}).get("extraction") or {}).get("window_paragraphs")


def content_paragraphs(parsed_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The chapter's narrative paragraphs (footnotes excluded - the rule events uses)."""
    from src.extraction.events import _is_footnote_paragraph

    paras = [
        p
        for p in (parsed_data or {}).get("paragraphs", []) or []
        if isinstance(p, dict)
        and isinstance(p.get("absolute_number"), int)
        and str(p.get("text") or "").strip()
    ]
    return [p for p in paras if not _is_footnote_paragraph(p, paras)]


def window_id(numbers: List[int]) -> str:
    """A window's id - its paragraph range ("P007-P009"): deterministic, short to echo."""
    return f"P{min(numbers):03d}-P{max(numbers):03d}"


def window_event(
    parsed_data: Dict[str, Any], size: int = DEFAULT_WINDOW_PARAGRAPHS
) -> Dict[str, Any]:
    """The chapter as an event whose "sub-events" are its paragraph windows: EventID the chapter's
    deterministic id, no names, no summaries - only the parsed text. The SAME object for the same
    parsed file, so a window request is byte-identical whenever it is built."""
    from src.utils.deterministic_ids import event_id

    paras = content_paragraphs(parsed_data)
    windows = []
    for i in range(0, len(paras), max(1, size)):
        chunk = paras[i : i + size]
        nums = [p["absolute_number"] for p in chunk]
        windows.append(
            {
                "Sub-eventID": window_id(nums),
                "Sub-event_summary": "",
                "Sub-event_fulltext": {
                    paragraph_key(p["absolute_number"]): p["text"] for p in chunk
                },
            }
        )
    section = parsed_data.get("section_id") or parsed_data.get("section") or "full"
    eid = event_id(parsed_data.get("book"), parsed_data.get("chapter_title"), section)
    # "Chapter": the parsed title, for prompts that name the chapter (weather) - parsed-only, so a
    # window request is the same before and after the events are in
    return {
        "Chapter": parsed_data.get("chapter_title") or "",
        "Event": {"EventID": eid, "Sub-events": windows},
    }


def window_event_for(event_file: Path, size: int) -> Optional[Dict[str, Any]]:
    """The window event of an event file's chapter (its sibling ``-parsed.json``), or None."""
    parsed_file = event_file.with_name(
        event_file.name.replace("-event.json", "-parsed.json")
    )
    try:
        return window_event(json.loads(parsed_file.read_text(encoding="utf-8")), size)
    except (
        Exception
    ) as e:  # noqa: BLE001 - unreadable or malformed: the caller's legacy path
        logger.warning(
            "OBSERV windows.no_parsed_file event=%s err=%s: %s",
            event_file.name,
            type(e).__name__,
            e,
        )
        return None


# --- attribution: a window's answer -> the real sub-events -------------------------------------


def paragraph_owners(event_data: Dict[str, Any]) -> Dict[int, str]:
    """{paragraph number: the real Sub-eventID holding it}."""
    owners: Dict[int, str] = {}
    for se in (event_data.get("Event") or {}).get("Sub-events", []) or []:
        for key in se.get("Sub-event_fulltext") or {}:
            n = paragraph_number(key)
            if n is not None:
                owners.setdefault(n, se.get("Sub-eventID", ""))
    return owners


def attribute(
    window: Dict[str, Any], obj: Dict[str, Any], owners: Dict[int, str]
) -> Tuple[Optional[str], List[int], bool]:
    """(real Sub-eventID, the paragraphs the item came from, whether its quote was found).

    The paragraphs are those of the window whose text contains the item's quote; when it is in none
    of them (a quote spanning two paragraphs, or not verbatim) all the window's paragraphs. The
    sub-event is the one holding the first of them; None when no sub-event holds any (the events
    answer left those paragraphs out)."""
    paras = {
        n: normalize(text)
        for k, text in (window.get("Sub-event_fulltext") or {}).items()
        if (n := paragraph_number(k)) is not None
    }
    quote = normalize(obj.get("original_text"))
    found = [n for n, text in sorted(paras.items()) if quote and quote in text]
    nums = found or sorted(paras)
    for n in nums:
        if owners.get(n):
            return owners[n], nums, bool(found)
    return None, nums, bool(found)


def regroup(
    response: Dict[str, Any],
    response_key: str,
    inner_key: str,
    window: Dict[str, Any],
    real_event: Dict[str, Any],
    entity_type: str = "",
) -> Dict[str, Any]:
    """A window's answer regrouped under the REAL sub-events its items belong to (``attribute``).
    Every item of a one-window request is that window's, whatever ``sub_event_id`` it echoes.
    Items no sub-event holds are dropped and counted; per-group extras (places' relationships) go
    with the first group."""
    owners = paragraph_owners(real_event)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    extras: Dict[str, List[Any]] = {}
    unowned = unquoted = 0
    for obj in _items(response, response_key, inner_key, extras):
        seid, _nums, quoted = attribute(window, obj, owners)
        unquoted += not quoted
        if seid is None:
            unowned += 1
        else:
            groups.setdefault(seid, []).append(obj)
    _observe(entity_type, window, unowned, unquoted)
    out = [{"sub_event_id": s, inner_key: objs} for s, objs in groups.items()]
    if out:
        out[0].update(extras)
    return {**(response or {}), response_key: out}


def _items(
    response: Dict[str, Any],
    response_key: str,
    inner_key: str,
    extras: Dict[str, List[Any]],
) -> List[Dict[str, Any]]:
    """The answer's entity items; each group's other list fields are collected into ``extras``."""
    items: List[Dict[str, Any]] = []
    for group in (response or {}).get(response_key, []) or []:
        if isinstance(group, dict):
            _collect_extras(group, inner_key, extras)
            items.extend(
                o for o in group.get(inner_key, []) or [] if isinstance(o, dict)
            )
    return items


def _collect_extras(
    group: Dict[str, Any], inner_key: str, extras: Dict[str, List[Any]]
) -> None:
    for k, v in group.items():
        if k not in ("sub_event_id", inner_key) and isinstance(v, list):
            extras.setdefault(k, []).extend(v)


def _observe(
    entity_type: str, window: Dict[str, Any], unowned: int, unquoted: int
) -> None:
    from src.utils.observe import observe_warn_counted

    if unowned:
        observe_warn_counted(
            entity_type or "entities",
            "window_unattributed",
            key=(entity_type,),
            window=window.get("Sub-eventID", ""),
            dropped=unowned,
            note="the events answer holds none of this window's paragraphs: its items have no "
            "sub-event and were dropped (check events coverage)",
        )
    if unquoted:
        # the item went to the sub-event of the window's FIRST paragraph: right unless the window
        # spans two sub-events
        logger.info(
            "OBSERV windows.quote_not_in_window entity=%s window=%s items=%d",
            entity_type,
            window.get("Sub-eventID", ""),
            unquoted,
        )
