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
from src.utils.quote_match import (
    EXACT,
    NEAR,
    fragments,
    locate,
    naming_sentence,
    normalize,
    source_passage,
)

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


def _window(chunk: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One window of parsed paragraphs as a one-"sub-event" view: its range id, no summary, the
    raw text."""
    return {
        "Sub-eventID": window_id([p["absolute_number"] for p in chunk]),
        "Sub-event_summary": "",
        "Sub-event_fulltext": {
            paragraph_key(p["absolute_number"]): p["text"] for p in chunk
        },
    }


def sub_event_windows(
    parsed_data: Dict[str, Any], real_event: Dict[str, Any], size: Optional[int] = None
) -> List[Tuple[str, Dict[str, Any]]]:
    """``[(real Sub-eventID, window)]``: each sub-event's OWN raw paragraphs in windows of
    ``size`` - a window never crosses a sub-event boundary, so a request whose answer carries no
    quote to attribute it by (supplemental's note classification) belongs to one sub-event.
    Paragraphs no sub-event holds are left out (as everywhere: the events answer omitted them).
    """
    size = max(1, size or window_size())
    owners = paragraph_owners(real_event)
    by_sub_event: Dict[str, List[Dict[str, Any]]] = {}
    for p in content_paragraphs(parsed_data):
        seid = owners.get(p["absolute_number"])
        if seid:
            by_sub_event.setdefault(seid, []).append(p)
    return [
        (seid, _window(paras[i : i + size]))
        for seid, paras in by_sub_event.items()
        for i in range(0, len(paras), size)
    ]


def window_event(
    parsed_data: Dict[str, Any], size: int = DEFAULT_WINDOW_PARAGRAPHS
) -> Dict[str, Any]:
    """The chapter as an event whose "sub-events" are its paragraph windows: EventID the chapter's
    deterministic id, no names, no summaries - only the parsed text. The SAME object for the same
    parsed file, so a window request is byte-identical whenever it is built."""
    from src.utils.deterministic_ids import event_id

    paras = content_paragraphs(parsed_data)
    windows = [_window(paras[i : i + size]) for i in range(0, len(paras), max(1, size))]
    section = parsed_data.get("section_id") or parsed_data.get("section") or "full"
    eid = event_id(parsed_data.get("book"), parsed_data.get("chapter_title"), section)
    # "Chapter": the parsed title, for prompts that name the chapter (weather) - parsed-only, so a
    # window request is the same before and after the events are in
    return {
        "Chapter": parsed_data.get("chapter_title") or "",
        "Event": {"EventID": eid, "Sub-events": windows},
    }


def window_event_for(event_file: Path, size: int) -> Optional[Dict[str, Any]]:
    """The window event of an event file's chapter (its sibling ``-parsed.json``), or None (no
    such sibling: a file not named ``*-event.json`` has none - never read the event file itself).
    """
    if not event_file.name.endswith("-event.json"):
        logger.warning(
            "OBSERV windows.no_parsed_file file=%s: not an *-event.json",
            event_file.name,
        )
        return None
    parsed_file = event_file.with_name(
        event_file.name[: -len("-event.json")] + "-parsed.json"
    )
    return chapter_windows(parsed_file, size)


def chapter_windows(
    parsed_file: Path, size: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """The window event of a parsed chapter file, or None when it is unreadable or malformed
    (the caller's per-sub-event path then, said in the log)."""
    try:
        return window_event(
            json.loads(Path(parsed_file).read_text(encoding="utf-8")),
            size or window_size(),
        )
    except (
        Exception
    ) as e:  # noqa: BLE001 - unreadable or malformed: the caller's legacy path
        logger.warning(
            "OBSERV windows.no_parsed_file file=%s err=%s: %s",
            getattr(parsed_file, "name", parsed_file),
            type(e).__name__,
            e,
        )
        return None


def unusable(wev: Dict[str, Any], real_event: Dict[str, Any], entity: str) -> bool:
    """True when a chapter's windows cannot be attributed, so nothing is asked: no real
    sub-events (every answer would be dropped), or sub-events but no windows (its parsed
    paragraphs are unusable) - the second a LOUD failure, never a fall back to the events' own
    text."""
    event = (real_event or {}).get("Event") or {}
    if not event.get("Sub-events"):
        logger.info(
            "OBSERV %s.skipped reason=no_sub_events event_id=%s",
            entity,
            event.get("EventID", "?"),
        )
        return True
    if (wev.get("Event") or {}).get("Sub-events"):
        return False
    return no_windows(real_event, entity)


def no_windows(real_event: Dict[str, Any], entity: str) -> bool:
    """A chapter with no windows to ask: a skip without sub-events, else a LOUD failure (its
    parsed paragraphs are unusable) - never a fall back to the events' own text. Always True.
    """
    event = (real_event or {}).get("Event") or {}
    if not event.get("Sub-events"):
        return True
    from src.utils.observe import observe_error

    observe_error(
        entity or "entities",
        "windows_empty",
        event_id=event.get("EventID", ""),
        note="the chapter has sub-events but no paragraph windows (parsed paragraphs "
        "missing absolute_number/text?): nothing asked - a count of 0 here is a FAILURE",
    )
    return True


def own_answer(window_id: str, found: Dict[str, Any], entity: str) -> Dict[str, Any]:
    """A ONE-window request's answer is all that window's, whatever key Grok echoes (a mangled
    key lost the window's items): ``{window_id: every item}``."""
    if not found:
        return found
    if set(found) != {window_id}:
        logger.info(
            "OBSERV windows.echo_rekeyed entity=%s window=%s echoed=%s",
            entity,
            window_id,
            ",".join(map(str, found))[:120],
        )
    return {window_id: [m for v in found.values() if isinstance(v, list) for m in v]}


def _field(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


# the keys an item names its entity by (people/equipment "name", places "current_name", groups
# "group_name"); a statement (date, weather, logistics, casualty) has none
_NAME_KEYS = ("name", "current_name", "group_name")
# the keys an item cites its paragraphs by (equipment/dates "paragraph_numbers", logistics
# "paragraphs")
_PARAGRAPH_KEYS = ("paragraph_numbers", "paragraphs")

# how an item's quote was settled (``settle``): verbatim; replaced by the source passage it nearly
# matched; replaced by the sentence naming the item; no quote given (kept, unverifiable)
REPAIRED, NAMED, UNQUOTED = "repaired", "named", "unquoted"


def _name_of(item: Any) -> str:
    return next((str(v) for k in _NAME_KEYS if (v := _field(item, k))), "")


def settle(
    quote: Any, name: Any, fulltext: Dict[str, Any]
) -> Optional[Tuple[str, List[int], str]]:
    """(the quote to store, the paragraphs it is in, how) for one item of an answer about the
    paragraphs ``fulltext`` (``{"Paragraph_<n>": text}``), or None when that text supports
    neither the item's quote nor its name - an item the source does not show.

    original_text is the provenance anchor: it must be IN the source. In order:

    * ``EXACT``: the quote is verbatim (whole words; an elided "A ... B" is its parts; across two
      consecutive paragraphs) - kept, with every paragraph it is in;
    * ``REPAIRED``: a near match (OCR noise or a model's rewording, ``quote_match.NEAR``) - the
      source's own sentence(s) instead (``source_passage``);
    * ``NAMED``: the first sentence naming the item (an entity's name, never a statement's);
    * ``UNQUOTED``: no quote given (or no words in it: "...") and no name in the text - kept as
      before, on all the paragraphs (a quote-less statement is still a statement of them).
    """
    raw = _paragraph_texts(fulltext)
    nums, how = locate(quote, [(n, normalize(t)) for n, t in sorted(raw.items())], raw)
    if how == EXACT:
        return str(quote), nums, EXACT
    passage = _passage(quote, raw, nums) if how == NEAR else None
    if passage:
        return passage[0], [passage[1]], REPAIRED
    named = _naming(name, raw)
    if named:
        return named[0], [named[1]], NAMED
    return ("", sorted(raw), UNQUOTED) if not fragments(quote) else None


def _paragraph_texts(fulltext: Dict[str, Any]) -> Dict[int, str]:
    """``{paragraph number: text}`` of a ``{"Paragraph_<n>": text}`` fulltext."""
    return {
        n: str(t or "")
        for k, t in (fulltext or {}).items()
        if (n := paragraph_number(k)) is not None
    }


def _passage(
    quote: Any, raw: Dict[int, str], nums: List[int]
) -> Optional[Tuple[str, int]]:
    """(the source passage a near-matched quote is, its paragraph): the first of the quote's
    paragraphs that holds one (an elided quote's exact part may come first)."""
    for n in nums:
        passage = source_passage(quote, raw[n])
        if passage:
            return passage, n
    return None


def _naming(name: Any, raw: Dict[int, str]) -> Optional[Tuple[str, int]]:
    """(the first sentence naming ``name``, its paragraph), or None (no name, or not named)."""
    for n, text in sorted(raw.items()) if name else ():
        sentence = naming_sentence(name, text)
        if sentence:
            return sentence, n
    return None


def settle_item(
    item: Any, fulltext: Dict[str, Any], entity: str, where: str
) -> Optional[List[int]]:
    """``settle`` one answer item (a dict or a model) IN PLACE: its original_text becomes the
    settled quote, its cited paragraphs (when it has the field) the ones the quote is in.
    Returns those paragraphs; None (item untouched) when the text supports neither its quote nor
    its name - the caller drops it."""
    answered = _field(item, "original_text")
    settled = settle(answered, _name_of(item), fulltext)
    if settled is None:
        return None
    quote, nums, how = settled
    _put(item, "original_text", quote or answered)
    for key in _PARAGRAPH_KEYS:
        _put(item, key, list(nums))
    if how in (REPAIRED, NAMED):
        logger.info(
            "OBSERV windows.quote_settled entity=%s how=%s where=%s answered=%r stored=%r",
            entity,
            how,
            where,
            str(answered or "")[:120],
            quote[:120],
        )
    return nums


def _put(item: Any, key: str, value: Any) -> None:
    """Set a field the item HAS (a dict key or a model field): a stray key would be an
    unmodeled field downstream; a model without the field would raise."""
    if isinstance(item, dict):
        if key in item or key == "original_text":
            item[key] = value
    elif key in (getattr(type(item), "model_fields", None) or {}):
        setattr(item, key, value)


def attribute_found(
    found: Dict[str, List[Any]],
    wev: Dict[str, Any],
    real_event: Dict[str, Any],
    entity: str,
) -> Dict[str, List[Any]]:
    """``{window id: items}`` -> ``{real Sub-eventID: items}``: each item (a dict or a model with
    ``original_text``) is settled against its window's text (``settle_item``) and goes to the
    sub-event holding its paragraphs (``attribute``); one the text does not support, or no
    sub-event holds, is dropped and counted. The statement extractors' form of ``regroup``.
    """
    from src.utils.deterministic_ids import reconcile_echoed_keys

    wins = (wev.get("Event") or {}).get("Sub-events") or []
    found = reconcile_echoed_keys([w["Sub-eventID"] for w in wins], found, entity)
    owners = paragraph_owners(real_event)
    out: Dict[str, List[Any]] = {}
    for w in wins:
        unowned = unsupported = 0
        for item in found.get(w["Sub-eventID"]) or []:
            nums = settle_item(
                item, w.get("Sub-event_fulltext") or {}, entity, w["Sub-eventID"]
            )
            if nums is None:
                unsupported += 1
                continue
            seid = attribute(nums, owners)
            if seid is None:
                unowned += 1
            else:
                out.setdefault(seid, []).append(item)
        observe_drops(entity, w, unowned, unsupported)
    return out


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


def attribute(nums: List[int], owners: Dict[int, str]) -> Optional[str]:
    """The real Sub-eventID holding the first of an item's paragraphs (``settle_item``), or None
    when no sub-event holds any (the events answer left them out)."""
    return next((owners[n] for n in nums if owners.get(n)), None)


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
    extras: Dict[str, List[Any]] = {}
    groups = attribute_found(
        {
            window.get("Sub-eventID", ""): _items(
                response, response_key, inner_key, extras
            )
        },
        {"Event": {"Sub-events": [window]}},
        real_event,
        entity_type,
    )
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


def observe_drops(
    entity_type: str, window: Dict[str, Any], unowned: int = 0, unsupported: int = 0
) -> None:
    """Count (and alert) the items of one window / sub-event's answer that were dropped: no
    sub-event holds their paragraphs (``unowned``), or the text does not support them
    (``unsupported``)."""
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
    if unsupported:
        observe_warn_counted(
            entity_type or "entities",
            "quote_unsupported",
            key=(entity_type,),
            window=window.get("Sub-eventID", ""),
            dropped=unsupported,
            note="the window's text holds neither the item's quote (not even nearly) nor its "
            "name: dropped as not in the source - if a window repeats here, check its parsed "
            "text (a split or garbled paragraph)",
        )
