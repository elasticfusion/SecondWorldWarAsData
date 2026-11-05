"""Supplemental material extraction from event data - Phase 1: Core Extraction."""

import json
import math
import logging
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional
import ulid
from jsonschema import ValidationError, validate

from src.grok_client import BatchModeCollecting, GrokClient
from src.utils.json_validator import _fix_invalid_ulids
from src.json_schemas import SUPPLEMENTAL_SCHEMA
from src.utils.observe import observe_warn

from src.utils.prompt_loader import get_system_prompt

logger = logging.getLogger(__name__)


def _build_people_index(output_root: Path) -> Dict[str, str]:
    """Build people name -> PersonID index."""
    from src.utils.entity_index import build_name_index

    return build_name_index(output_root / "people", "PersonID", "name")


def _build_groups_index(output_root: Path) -> Dict[str, str]:
    """Build group name -> GroupID index (includes aliases)."""
    from src.utils.entity_index import build_name_index

    return build_name_index(
        output_root / "people_groups", "PeopleGroupID", "group_name"
    )


def _resolve_author_ids(
    authors: List[str], people_index: Dict[str, str]
) -> List[Optional[str]]:
    """Resolve author names to PersonIDs."""
    return [people_index.get(name.lower()) for name in authors]


def _resolve_mentioned_people(
    citation_text: str, people_index: Dict[str, str]
) -> List[Dict[str, str]]:
    """Extract people mentioned in citation text."""
    mentioned = []
    text_lower = citation_text.lower()
    for name, person_id in people_index.items():
        if name in text_lower:
            mentioned.append({"PersonID": person_id, "name": name})
    return mentioned


def _resolve_mentioned_organizations(
    citation_text: str, groups_index: Dict[str, str]
) -> List[Dict[str, str]]:
    """Extract organizations mentioned in citation text."""
    mentioned = []
    text_lower = citation_text.lower()
    for name, group_id in groups_index.items():
        if name in text_lower:
            mentioned.append({"PeopleGroupID": group_id, "name": name})
    return mentioned


def _build_ref_context(
    endnote_refs: List,
    footnote_refs: List,
    endnote_texts: Optional[Dict[int, str]],
) -> tuple:
    """Build endnote text block and type hint for a sub-event."""
    from src.extraction.fetch_endnotes import format_endnote_text_block

    all_refs = [r for r in endnote_refs + footnote_refs if isinstance(r, int)]
    endnote_block = ""
    if endnote_texts and all_refs:
        endnote_block = format_endnote_text_block(endnote_texts, all_refs)
        return endnote_block, ""

    ref_type_hint = ""
    if endnote_refs and not footnote_refs:
        ref_type_hint = "\nNote: These are endnotes (on a separate page from the text)."
    elif footnote_refs and not endnote_refs:
        ref_type_hint = (
            "\nNote: These are footnotes (at the bottom of the same page as the text)."
        )
    return endnote_block, ref_type_hint


def create_supplemental_prompt(
    event_data: Dict[str, Any],
    endnote_texts: Optional[Dict[int, str]] = None,
) -> List[tuple]:
    """Create prompt for supplemental material extraction."""
    event_name = event_data.get("Event", {})
    event_id = event_name.get("EventID", "")
    event_title = event_name.get("Event_Name", "")

    prompts = []

    for sub_event in event_name.get("Sub-events", []):
        sub_event_id = sub_event.get("Sub-eventID", "")
        sub_event_summary = sub_event.get("Sub-event_summary", "")
        fulltext = sub_event.get("Sub-event_fulltext", {})
        endnote_refs = sub_event.get("Endnote_References", [])
        footnote_refs = sub_event.get("Footnote_References", [])

        text = "\n".join(fulltext.values())
        endnote_block, ref_type_hint = _build_ref_context(
            endnote_refs, footnote_refs, endnote_texts
        )

        from src.utils.prompt_loader import get_system_prompt, render_prompt

        prompt = render_prompt(
            "supplemental",
            event_title=event_title,
            event_id=event_id,
            sub_event_summary=sub_event_summary,
            sub_event_id=sub_event_id,
            text=text,
            endnote_refs=endnote_refs,
            footnote_refs=footnote_refs,
            endnote_block=endnote_block,
            ref_type_hint=ref_type_hint,
        )
        # Deterministic identity for cache keying — DELIBERATELY excludes the volatile
        # `text` (Sub-event_fulltext is regenerated/summarized differently between a
        # batch submit and the retrieve re-run) AND the endnote_block (same volatility
        # class — must not reintroduce a submit-vs-rerun mismatch). The event/sub-event
        # ids are deterministic content-derived ULIDs and, with the sorted reference
        # numbers, uniquely identify the sub-event, so a submit and its retrieve resolve
        # to the SAME supplemental cache entry.
        stable_material = "|".join(
            [
                str(event_id),
                str(sub_event_id),
                "e:"
                + ",".join(
                    str(n)
                    for n in sorted(
                        r for r in (endnote_refs or []) if isinstance(r, int)
                    )
                ),
                "f:"
                + ",".join(
                    str(n)
                    for n in sorted(
                        r for r in (footnote_refs or []) if isinstance(r, int)
                    )
                ),
            ]
        )
        prompts.append((sub_event_id, prompt, stable_material, event_id))

    return prompts


def _is_cross_reference(title: str) -> bool:
    """Check if a title is actually a cross-reference, not a document title."""
    if not title:
        return False
    t = title.lower().strip().rstrip(".")
    if t in ("ibid", "ibid.", "op. cit", "op cit", "loc. cit", "loc cit"):
        return True
    if t.startswith("ibid") and len(title) < 20:
        return True
    if t.startswith("see chap") or t.startswith("see p.") or t.startswith("see above"):
        return True
    return False


def _resolve_reference_id(
    material: Dict[str, Any],
    field: str,
    known_id: str,
    *,
    material_id: str,
) -> None:
    """Resolve a cross-reference ID field (EventID / Sub-eventID) on a material.

    A supplemental material's EventID/Sub-eventID are REFERENCES to the owning event/sub-event —
    NOT primary keys this record is free to invent. The project rule is to never mint a random
    identity for a reference (that produces a DANGLING cross-ref no entity resolves to). So:
      1. If the LLM echoed a value, keep it (it is validated/reconciled elsewhere).
      2. Else if we KNOW the real parent id (threaded from the extraction context), DERIVE from it.
      3. Else (no echo AND no known parent) mint a last-resort ULID but emit a LOUD observe_error so
         the dangling reference is a greppable anomaly instead of a silent corpus defect.
    """
    if material.get(field) is not None:
        return
    if known_id:
        material[field] = known_id
        return
    import ulid

    from src.utils.observe import observe_error

    minted = str(ulid.new())
    material[field] = minted
    observe_error(
        "supplemental",
        "reference_id_fabricated",
        field=field,
        material_id=material_id,
        minted=minted,
        note=(
            "no LLM-echoed value AND no known parent id in context — minted a last-resort "
            "ULID; this reference is DANGLING (no event/sub-event resolves to it). Thread the "
            "real parent id into sanitize_supplemental_data to eliminate"
        ),
    )


def _sanitize_material(
    material: Dict[str, Any],
    sub_event_id: str = "",
    *,
    known_event_id: str = "",
) -> None:
    """Sanitize a single supplemental material (modifies in place).

    ``sub_event_id`` / ``known_event_id`` are the REAL parent ids from the extraction context; when
    the LLM omitted a reference, the material inherits the known parent rather than a random ULID
    (derive-not-fabricate). See ``_resolve_reference_id``.
    """
    _apply_defaults(
        material, known_event_id=known_event_id, known_sub_event_id=sub_event_id
    )
    _normalize_enums(material)
    _normalize_urls(material)
    _normalize_citation(material, sub_event_id)


def _apply_defaults(
    material: Dict[str, Any],
    *,
    known_event_id: str = "",
    known_sub_event_id: str = "",
) -> None:
    """Set required string fields to defaults if missing.

    MaterialID is a PRIMARY key for this record, so a fresh ULID is correct. EventID/Sub-eventID are
    REFERENCES — resolved via ``_resolve_reference_id`` (keep echo → derive from known parent →
    loud last-resort mint), never silently randomized. ID fields get a REAL ULID (not the
    GENERATE_NEW_ULID sentinel) because this record is validated without a guaranteed generate_ulids
    pass afterward — an empty string or the literal sentinel both fail the strict ULID pattern.
    """
    import ulid

    # The LLM emits the class as "classification" (prompt); routing reads "content_class".
    # Reconcile BEFORE defaulting so the LLM's real class (citation/prose/hybrid) actually
    # drives the split — otherwise content_class is always absent -> always defaulted ->
    # every endnote (incl. hybrids) misroutes to the bibliography and the prose half is lost.
    if material.get("content_class") is None and material.get("classification"):
        material["content_class"] = material["classification"]

    if material.get("MaterialID") is None:
        material["MaterialID"] = str(ulid.new())
    # References: derive from the known parent rather than fabricate (never-dangle rule).
    _resolve_reference_id(
        material, "EventID", known_event_id, material_id=material["MaterialID"]
    )
    _resolve_reference_id(
        material,
        "Sub-eventID",
        known_sub_event_id,
        material_id=material["MaterialID"],
    )

    defaults = {
        # Unknown class -> human review (NOT document_reference: silently storing an
        # unclassified endnote as a citation, or dropping a prose one, is worse than a
        # review-queue entry).
        "content_class": lambda: "ambiguous",
        "reference_type": lambda: "bibliography",
        "verbatim_reference": lambda: "",
        "availability": lambda: "unknown",
    }
    for key, make_default in defaults.items():
        if material.get(key) is None:
            material[key] = make_default()


def _normalize_enums(material: Dict[str, Any]) -> None:
    """Validate enum fields to allowed values."""
    valid_classes = {
        "citation",
        "prose",
        "hybrid",
        "document_reference",
        "factual_content",
        "ambiguous",
    }
    if material.get("content_class") not in valid_classes:
        # An invalid/unknown class -> 'ambiguous' (human-review queue), NOT 'citation' — coercing a
        # typo/garbage class to 'citation' misroutes it to the bibliography as a real document.
        material["content_class"] = "ambiguous"

    ref_type = material.get("reference_type", "")
    if ref_type == "map":
        material["reference_type"] = "endnote"
    elif ref_type not in ("endnote", "footnote", "bibliography"):
        logger.warning(
            "Invalid reference_type '%s', defaulting to 'bibliography'", ref_type
        )
        material["reference_type"] = "bibliography"


def _normalize_urls(material: Dict[str, Any]) -> None:
    """Normalize resource_url singular → resource_urls array."""
    if "resource_url" in material and "resource_urls" not in material:
        url = material.pop("resource_url")
        material["resource_urls"] = [url] if url else []
    elif "resource_url" in material:
        material.pop("resource_url")
    if material.get("resource_urls") is None:
        material["resource_urls"] = []


def _normalize_citation(material: Dict[str, Any], sub_event_id: str = "") -> None:
    """Ensure citation has required fields and derive title from verbatim if needed."""
    citation = material.get("citation")
    if not isinstance(citation, dict):
        citation = {}
    material["citation"] = citation
    if citation.get("title") is not None and not isinstance(citation["title"], str):
        citation["title"] = _scalar_text(
            citation["title"]
        )  # verbatim, else None -> derived

    if citation.get("title") is None or _is_cross_reference(citation.get("title", "")):
        citation["title"] = _derive_title_from_mentions(material.get("mentions") or [])

    _coerce_citation_types(citation, sub_event_id)


# Key names the prompt historically used for schema properties (prompt/schema drift). Mapped so
# their values are no longer silently ignored downstream (e.g. supplemental_advanced reads "isbn").
_CITATION_KEY_ALIASES = {
    "ISBN": "isbn",
    "location": "publication_location",
    "country": "publication_country",
}


_CITATION_STRING_FIELDS: Optional[tuple] = None


def _citation_string_fields() -> tuple:
    """Schema-derived ``["string","null"]`` citation fields (cached; never drifts from schema)."""
    global _CITATION_STRING_FIELDS
    if _CITATION_STRING_FIELDS is None:
        schema: Dict[str, Any] = dict(
            SUPPLEMENTAL_SCHEMA
        )  # typed as object at module level
        items: Dict[str, Any] = schema["properties"]["Supplemental_Material"]["items"]
        props: Dict[str, Any] = (
            items["properties"].get("citation", {}).get("properties", {})
        )
        _CITATION_STRING_FIELDS = tuple(
            k
            for k, v in props.items()
            if "string" in (v.get("type") or []) and k != "title"
        )
    return _CITATION_STRING_FIELDS


def _scalar_text(x: Any) -> Optional[str]:
    """Verbatim text of a scalar (integral floats without '.0'); None for null/blank/bool/other."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    if isinstance(x, (str, int, float)):
        t = str(x)
        return t if t.strip() else None
    return None


def _as_text(v: Any) -> Any:
    """Coerce a value to the schema's ``string|null`` VERBATIM. Lists join their scalar items with
    '; ' (null/blank dropped). Non-scalars that cannot be rendered verbatim become None — the field
    is lost, but never the whole sub-event."""
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, list):
        parts = [t for t in (_scalar_text(x) for x in v) if t is not None]
        return "; ".join(parts) or None
    return _scalar_text(v)


def _map_aliases(citation: Dict[str, Any], fixed: List[str]) -> None:
    for old, new in _CITATION_KEY_ALIASES.items():
        if old not in citation:
            continue
        val = citation.pop(old)
        if citation.get(new) in (None, "", []):
            citation[new] = val
            fixed.append(f"{old}->{new}")
        elif val not in (None, "", []) and val != citation[new]:
            fixed.append(f"{old}->{new}(conflict_dropped)")


def _coerce_author(citation: Dict[str, Any], fixed: List[str]) -> None:
    author = citation.get("author")
    if isinstance(author, list):
        clean = [t for t in (_scalar_text(x) for x in author) if t is not None]
    else:
        # A lone string is wrapped VERBATIM — "Rapport, Leonard and Norwood, Arthur Jr." stays
        # one entry (splitting would invent structure).
        t = _scalar_text(author)
        clean = [t] if t is not None else []
    if clean != author:
        if author is not None:
            fixed.append("author")
        citation["author"] = clean


def _coerce_isbn(citation: Dict[str, Any], fixed: List[str]) -> None:
    """Keep ``isbn`` a single parseable ISBN: first value stays, the rest go to isbn_edition."""
    isbn = citation.get("isbn")
    if not isinstance(isbn, list):
        return
    parts = [t for t in (_scalar_text(x) for x in isbn) if t is not None]
    citation["isbn"] = parts[0] if parts else None
    if parts[1:] and citation.get("isbn_edition") in (None, ""):
        citation["isbn_edition"] = "; ".join(parts[1:])
        fixed.append("isbn")
    elif parts[1:]:
        fixed.append("isbn(extra_dropped)")
    else:
        fixed.append("isbn")


def _coerce_citation_types(citation: Dict[str, Any], sub_event_id: str = "") -> None:
    """Coerce common LLM type/key slips to the schema, VERBATIM (never invent or split).

    Validation errors are not retried, so ONE mistyped field dropped the WHOLE sub-event's materials
    (XChannel ch8 8b: author as a string -> 9/9 sub-events, 0 of 102 endnotes). One counted OBSERV
    warn per material names the response's Sub-eventID and the fields touched."""
    fixed: List[str] = []
    _map_aliases(citation, fixed)
    _coerce_author(citation, fixed)
    _coerce_isbn(citation, fixed)
    for field in _citation_string_fields():
        if field in citation:
            val = _as_text(citation[field])
            if val != citation[field] or type(val) is not type(citation[field]):
                fixed.append(f"{field}:{type(citation[field]).__name__}")
                citation[field] = val
    if fixed:
        observe_warn(
            "supplemental",
            "citation_coerced",
            sub_event=(sub_event_id or "?")[:40],
            fields=",".join(fixed)[:200],
            note="LLM type/key slips coerced to the schema so the sub-event's materials persist",
        )


def _derive_title_from_mentions(mentions: list) -> str:
    """Derive a citation title from the first non-cross-reference verbatim mention,
    stripping leading note-number / marker prefixes. Returns 'Unknown' if none usable.
    """
    verbatim = ""
    for m in mentions:
        v = m.get("verbatim_reference", "")
        if v and not _is_cross_reference(v):
            verbatim = v
            break
    if not verbatim:
        return "Unknown"
    import re

    title = re.sub(r"^\d+[\.\:\)]\s*", "", verbatim)
    title = re.sub(r"^[\*\†\‡\§\¶\#]+\s*", "", title).strip()[:200]
    return title if title else "Unknown"


def _merge_inner_wrappers(inner: list):
    """Merge the Supplemental_Material from each keyed inner wrapper ({"<key>": {Supplemental_
    Material:[..]}}) + carry the first wrapper's event-level ids. Returns (base, merged).
    """
    merged: list = []
    base: Dict[str, Any] = {}
    for v in inner:
        merged.extend(v.get("Supplemental_Material") or [])
        for k in ("Event_Name", "EventID", "Sub-event_Name", "Sub-eventID"):
            base.setdefault(k, v.get(k))
    return base, merged


def _is_keyed_material_list(k, v) -> bool:
    """True if (k, v) is a keyed material-list wrapper entry: a non-metadata string key
    mapping to a non-empty list of dict items."""
    if not isinstance(k, str) or k.startswith("_"):
        return False
    return isinstance(v, list) and bool(v) and all(isinstance(it, dict) for it in v)


def _flatten_keyed_lists(data: Dict[str, Any]) -> "tuple[list, Optional[str]]":
    """Collect material items from the key->LIST wrapper shape: a response keyed by a
    Sub-eventID OR a content-class name (e.g. {"Equipment": [..]} / {"<ULID>": [..]}) mapping
    directly to a list of dict material items. Skips metadata keys (``_*``). These shapes were
    previously rejected ('<key> does not match ^_') and the sub-event's bibliography DROPPED.

    PROVENANCE: when a key is itself a ULID it IS the real Sub-eventID — stamp it onto each of its
    materials' 'Sub-eventID' (unless already set) and return it as a detected base Sub-eventID, so
    the flattened materials keep their true sub-event linkage instead of being defaulted to a random
    ULID (which would dangle). A content-class key (e.g. 'Equipment') carries no ID and is dropped.
    """
    from src.schemas import ULID_REGEX as _ULID  # single ULID source

    out: list = []
    detected_seid: Optional[str] = None
    for k, v in data.items():
        if not _is_keyed_material_list(k, v):
            continue
        if _ULID.match(k):  # k is a str here (guaranteed by _is_keyed_material_list)
            detected_seid = detected_seid or k
            for it in v:
                it.setdefault("Sub-eventID", k)
        out.extend(v)
    return out, detected_seid


def _unwrap_keyed_supplemental(data: Dict[str, Any]) -> Dict[str, Any]:
    """Some LLM responses wrap output as a dict KEYED BY Sub-eventID ({"<ULID>": {...}, ...})
    instead of the flat single-sub-event shape. Flatten/merge into the flat shape so it validates
    (previously rejected as "<ULID> do not match '^_'" -> the sub-event's supplemental was dropped).
    Returns the (possibly rewritten) data unchanged when it is already flat."""
    if not isinstance(data, dict) or "Supplemental_Material" in data:
        return data
    inner = [
        v for v in data.values() if isinstance(v, dict) and "Supplemental_Material" in v
    ]
    # Also handle the key->LIST shape (keyed by Sub-eventID OR content-class name mapping directly
    # to a list of material items, no inner Supplemental_Material wrapper) — previously dropped.
    list_materials, detected_seid = _flatten_keyed_lists(data)
    if not inner and not list_materials:
        return data
    base, merged = _merge_inner_wrappers(inner)
    merged.extend(list_materials)
    # Preserve the sub-event linkage when a ULID key carried it (provenance): use the detected
    # Sub-eventID as the base unless an inner wrapper already supplied one.
    if detected_seid and not base.get("Sub-eventID"):
        base["Sub-eventID"] = detected_seid
    # NOTE: if multiple sub-events are wrapped together, their materials are MERGED and the
    # event-level ids collapse to the FIRST sub-event's — the caller extracts per sub-event, so a
    # multi-sub-event wrapper is itself the anomaly. Materials are preserved (not lost). A ULID key
    # is stamped onto its materials' Sub-eventID (provenance kept); a content-class key carries no
    # id, so those materials fall back to the base/defaulted ids (best-effort).
    return {**base, "Supplemental_Material": merged}


def sanitize_supplemental_data(
    data: Dict[str, Any],
    *,
    known_event_id: str = "",
    known_sub_event_id: str = "",
) -> Dict[str, Any]:
    """Sanitize supplemental data to ensure schema compliance.

    ``known_event_id`` / ``known_sub_event_id`` are the REAL parent ids from the extraction
    context. When the LLM omits an EventID/Sub-eventID the record DERIVES from these rather than
    minting a random ULID (which would dangle). If a known id is absent the last-resort mint emits a
    loud observe_error (see ``_resolve_reference_id``)."""
    data = _unwrap_keyed_supplemental(data)

    # Normalize plural key variant (LLM sometimes returns "Supplemental_Materials")
    if "Supplemental_Materials" in data and "Supplemental_Material" not in data:
        data["Supplemental_Material"] = data.pop("Supplemental_Materials")

    # Event-level references: derive from the known parent ids (never-dangle rule). A missing
    # reference with no known parent is minted loud via _resolve_reference_id, not silently.
    # A keyed-by-ULID unwrap may have recovered a real Sub-eventID into data; prefer the explicitly
    # threaded known id, else what the data already carries.
    eff_sub = known_sub_event_id or str(data.get("Sub-eventID") or "")
    if data.get("Event_Name") is None:
        data["Event_Name"] = ""
    if data.get("Sub-event_Name") is None:
        data["Sub-event_Name"] = ""
    _resolve_reference_id(data, "EventID", known_event_id, material_id="<event-level>")
    _resolve_reference_id(data, "Sub-eventID", eff_sub, material_id="<event-level>")

    # Sanitize each material, inheriting the resolved event/sub-event ids as the known parents.
    parent_event = str(data.get("EventID") or "")
    parent_sub = str(data.get("Sub-eventID") or "")
    for material in data.get("Supplemental_Material", []):
        _sanitize_material(material, parent_sub, known_event_id=parent_event)

    return data


def _coerce_supplemental_schema_keys(data: Dict[str, Any]) -> None:
    """Strip top-level keys SUPPLEMENTAL_SCHEMA does NOT permit, IN PLACE, so one stray LLM field
    no longer trips additionalProperties:False and SILENTLY drops the WHOLE sub-event's materials
    (#671 class — the whole-event validate + break->return None amplifies one bad key into total
    loss). Each strip is a LOUD canonical OBSERV. Fail-safe: never raises into extraction.
    """
    try:
        from src.schemas import strip_unmodeled_keys

        def _obs(field: str) -> None:
            observe_warn(
                "supplemental",
                "extra_field_stripped",
                sub_event=str(data.get("Sub-eventID") or "?")[:40],
                field=field,
                note="unmodeled top-level field removed so the sub-event's materials persist "
                "(additionalProperties) — widen the schema if this field is wanted",
            )

        strip_unmodeled_keys(data, SUPPLEMENTAL_SCHEMA, _obs)
    except Exception as e:  # noqa: BLE001 - normalization must never break extraction
        logger.debug("supplemental schema-key coercion skipped: %s", e)


def validate_supplemental_json(data: Dict[str, Any]) -> None:
    """Validate supplemental material JSON against schema."""
    validate(instance=data, schema=SUPPLEMENTAL_SCHEMA)


def generate_ulids(data: Any) -> Any:
    """Replace GENERATE_NEW_ULID placeholders with actual ULIDs."""
    if isinstance(data, dict):
        return {k: generate_ulids(v) for k, v in data.items()}
    if isinstance(data, list):
        return [generate_ulids(item) for item in data]
    if isinstance(data, str) and data == "GENERATE_NEW_ULID":
        return str(ulid.new())
    return data


def _append_to_file(
    file_path: Path, new_subevents: list[Dict[str, Any]], file_type: str
) -> None:
    """Append new sub-events to a JSON file, avoiding duplicates."""
    if not file_path.exists():
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        def _dedup_key(se: dict) -> str:
            # Deterministic identity: prefer the Sub-eventID; else reference_source; else a stable
            # hash of the content. The old key was reference_source ALONE — narrative sub-events
            # don't set it, so `None not in {...}` was always True and they re-appended every
            # submit/retrieve/reconcile pass (unbounded duplication, PATTERN 4).
            sid = se.get("Sub-eventID") or se.get("Sub_eventID")
            if sid:
                return f"sid:{sid}"
            rs = se.get("reference_source")
            if rs:
                return f"rs:{rs}"
            import hashlib as _h

            blob = json.dumps(
                {
                    "s": se.get("Sub-event_summary") or se.get("summary"),
                    "v": se.get("verbatim_reference"),
                    "t": se.get("Sub-event_fulltext"),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            return "h:" + _h.sha256(blob.encode("utf-8")).hexdigest()[:16]

        existing_keys = {_dedup_key(se) for se in data["Event"]["Sub-events"]}
        to_add = [se for se in new_subevents if _dedup_key(se) not in existing_keys]

        if not to_add:
            logger.info(
                "OBSERV supplemental.append file=%s candidates=%d added=0 (all dupes)",
                file_path.name,
                len(new_subevents),
            )
            return

        # Append and write
        data["Event"]["Sub-events"].extend(to_add)
        from src.utils.file_lock import write_json_with_lock

        write_json_with_lock(file_path, data, entity="events")

        logger.info(
            "OBSERV supplemental.append file=%s candidates=%d added=%d",
            file_path.name,
            len(new_subevents),
            len(to_add),
        )

    except Exception as e:
        logger.error("Error updating %s file: %s", file_type, e)


def append_subevents_to_files(
    event_file: Path, new_subevents: list[Dict[str, Any]]
) -> None:
    """Append new sub-events to event.json and parsed.json files."""
    if not new_subevents:
        return

    # Update event file
    _append_to_file(event_file, new_subevents, "event")

    # Update parsed file
    parsed_file = event_file.parent / event_file.name.replace(
        "-event.json", "-parsed.json"
    )
    _append_to_file(parsed_file, new_subevents, "parsed")


def _load_event_data(event_file: Path) -> Optional[Dict[str, Any]]:
    """Load and validate event data from file."""
    try:
        with open(event_file, "r", encoding="utf-8") as f:
            event_data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error("Failed to load event file %s: %s", event_file, e)
        return None

    if "Event" not in event_data:
        logger.error(
            "Invalid event file structure in %s: missing 'Event' key", event_file
        )
        return None

    return event_data


def _extract_with_retry(
    prompt: str,
    sub_event_id: str,
    grok_client: GrokClient,
    max_retries: int = 3,
    stable_key_material: str = "",
    known_event_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Extract supplemental material with retry logic.

    ``stable_key_material`` (optional) is a deterministic string identifying the request
    independently of the volatile prompt text (the supplemental prompt embeds regenerated
    sub-event fulltext/ids that differ between a batch submit and the later retrieve
    re-run). When provided it is hashed into the cache key so submit and retrieve resolve
    to the SAME cache entry — the fix for 0-bibliography (prompt-non-determinism cache miss).
    """
    cache_key = None
    if stable_key_material:
        # Fold the resolved model + temperature into the key so a model/temperature
        # change auto-invalidates stale entries (the volatile prompt text is excluded
        # by design; model/temp are the only non-prompt knobs that affect the answer).
        model = grok_client._get_model(
            "supplemental"
        )  # pylint: disable=protected-access
        key_input = f"supplemental:v1:{model}:0.1:{stable_key_material}"
        cache_key = hashlib.sha256(key_input.encode("utf-8")).hexdigest()
    for attempt in range(max_retries):
        try:
            response = grok_client.extract_json(
                prompt=prompt,
                system_prompt=get_system_prompt("supplemental"),
                temperature=0.1,
                use_cache=(attempt == 0),
                cache_type="supplemental",
                cache_key=cache_key,
            )

            response = generate_ulids(response)
            response = _fix_invalid_ulids(response)
            response = sanitize_supplemental_data(
                response,
                known_event_id=known_event_id,
                known_sub_event_id=sub_event_id,
            )
            # Strip unmodeled top-level keys BEFORE validate so one stray LLM field doesn't trip
            # additionalProperties:False and drop ALL materials for this sub-event (#671 class).
            if isinstance(response, dict):
                _coerce_supplemental_schema_keys(response)

            try:
                validate_supplemental_json(response)
            except ValidationError as e:
                logger.error(
                    "Validation error for sub-event %s: %s", sub_event_id, e.message
                )
                logger.debug("Invalid data: %s", json.dumps(response, indent=2))
                break  # Don't retry validation errors

            return response

        except BatchModeCollecting:
            # Batch COLLECT pass: the request was queued, not answered. Must NOT be
            # swallowed as a failure (that let the caller mark the event "processed"
            # during collection, so the batch RE-RUN then skipped it and never wrote
            # bibliography — Blocker B). Propagate so the caller leaves it unprocessed.
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            if attempt < max_retries - 1:
                logger.warning(
                    "  ⚠ Attempt %d failed for sub-event %s: %s",
                    attempt + 1,
                    sub_event_id,
                    e,
                )
                logger.info("  Retrying (%d/%d)...", attempt + 2, max_retries)
            else:
                logger.error(
                    "  ✗ All %d attempts failed for sub-event %s: %s",
                    max_retries,
                    sub_event_id,
                    e,
                )

    return None


def _supplemental_url_verified(title: str, url: str, grok_client: Any) -> bool:
    """Content-verify a search-found URL against the citation before it is stored as a supplemental
    resource_url. Reuses bibliography_resolver._verify_match (Grok fetches the page + confirms it
    matches), so the supplemental path gets the SAME verification gate the bibliography path has.
    Fail-open ONLY when there is no grok_client (offline/test) — matching _verify_match's own
    no-client behavior; any verifier error inside _verify_match is already caught there.
    """
    try:
        from src.enrichment.bibliography_resolver import _verify_match

        return _verify_match(title or "", url, grok_client)
    except Exception as e:  # noqa: BLE001 - verification must never crash enrichment
        logger.debug("supplemental url verify errored for %s: %s", url, e)
        # Fail-CLOSED on an unexpected verifier import/call error when a client WAS available:
        # better to drop an unverified url than to store a possibly-wrong source.
        return grok_client is None


def _enrich_material(
    material: Dict[str, Any],
    grok_client: Any,
    openserp_url: str = "http://localhost:7001",
) -> None:
    """Enrich material with searches and copyright calculation (modifies in place)."""
    from src.extraction.copyright_calculator import determine_license
    from src.extraction.supplemental_search import sequential_search

    category = material.get("material_category", "referenced_material")

    # Only enrich referenced material
    if category != "referenced_material":
        return

    citation = material.get("citation", {})
    title = citation.get("title", "")
    author = citation.get("author", [])
    author_str = author[0] if author else None
    periodical = citation.get("periodical_name")

    # Search for URLs if not already present
    if not material.get("resource_urls"):
        url, source = sequential_search(
            title=title,
            author=author_str,
            periodical=periodical,
            grok_client=grok_client,
            openserp_url=openserp_url,
            search_gutenberg=True,
        )

        if url and _supplemental_url_verified(title, url, grok_client):
            material["resource_urls"] = [url]
            material["search_source"] = source
            logger.info("Found URL via %s: %s", source, url)
        elif url:
            # A search hit that does NOT verify against the citation is DROPPED — sequential_search
            # returns the FIRST hit per source with no content-match, so storing it unchecked
            # silently attributes a wrong source to the citation (§1.0d HIGH). Mirror the
            # bibliography_resolver verify gate.
            logger.warning(
                "OBSERV supplemental.url_rejected source=%s title=%r url=%s — "
                "search hit did not verify against the citation; not stored",
                source,
                str(title)[:80],
                str(url)[:120],
            )

    # Calculate copyright
    license_status, license_notes = determine_license(material)
    if not material.get("license") or material.get("license") == "unknown":
        material["license"] = license_status
        material["license_notes"] = license_notes


def _resolve_entities_in_materials(
    materials: List[Dict[str, Any]],
    people_index: Dict[str, str],
    groups_index: Dict[str, str],
) -> None:
    """Resolve entity references in supplemental materials (modifies in place)."""
    for material in materials:
        citation = material.get("citation", {})
        authors = citation.get("author", [])
        verbatim = material.get("verbatim_reference", "")

        if authors:
            citation["author_ids"] = _resolve_author_ids(authors, people_index)

        mentioned_people = _resolve_mentioned_people(verbatim, people_index)
        mentioned_orgs = _resolve_mentioned_organizations(verbatim, groups_index)

        if mentioned_people:
            material["mentioned_people"] = mentioned_people
        if mentioned_orgs:
            material["mentioned_organizations"] = mentioned_orgs


def _enrich_materials(materials: list, grok_client: Any, enrich: bool) -> None:
    """Enrich materials with searches and copyright if enabled."""
    if enrich and grok_client:
        for material in materials:
            _enrich_material(material, grok_client)


def _filter_by_reference_type(materials: list, ref_type: str) -> list:
    """Filter materials by reference type."""
    return [m for m in materials if m.get("reference_type") == ref_type]


def _create_event_metadata(sub_event_data: dict) -> dict:
    """Create event metadata dictionary."""
    return {
        "Event_Name": sub_event_data.get("Event_Name"),
        "EventID": sub_event_data.get("EventID"),
        "Sub-event_Name": sub_event_data.get("Sub-event_Name"),
        "Sub-eventID": sub_event_data.get("Sub-eventID"),
    }


def _separate_by_type(
    all_supplemental: List[Dict[str, Any]],
    people_index: Dict[str, str],
    groups_index: Dict[str, str],
    grok_client: Any = None,
    enrich: bool = True,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Separate supplemental materials into endnotes and footnotes, resolving entities."""
    endnotes = []
    footnotes = []

    for sub_event_data in all_supplemental:
        # Normalize plural key variant
        if (
            "Supplemental_Materials" in sub_event_data
            and "Supplemental_Material" not in sub_event_data
        ):
            sub_event_data["Supplemental_Material"] = sub_event_data.pop(
                "Supplemental_Materials"
            )
        materials = sub_event_data.get("Supplemental_Material", [])

        # Resolve entities
        _resolve_entities_in_materials(materials, people_index, groups_index)

        # Enrich with searches and copyright
        _enrich_materials(materials, grok_client, enrich)

        # Filter by type
        endnote_materials = _filter_by_reference_type(materials, "endnote")
        footnote_materials = _filter_by_reference_type(materials, "footnote")

        # Create event metadata
        event_metadata = _create_event_metadata(sub_event_data)

        if endnote_materials:
            endnotes.append(
                {**event_metadata, "Supplemental_Material": endnote_materials}
            )

        if footnote_materials:
            footnotes.append(
                {**event_metadata, "Supplemental_Material": footnote_materials}
            )

    return endnotes, footnotes


def _get_source_copyright_year(event_file: Path) -> Optional[int]:
    """Get copyright year from the source book's metadata."""
    book_dir = event_file.parent.name  # e.g. "BreakoutAndPursuit"
    content_root = Path("contentrepository") / book_dir
    if not content_root.exists():
        return None
    # Check first chapter's metadata for copyright_date
    for meta in sorted(content_root.rglob("*-meta.yaml")):
        try:
            import yaml

            with open(meta, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            year = data.get("copyright_date")
            if year:
                return int(str(year).strip("'\""))
        except (ValueError, OSError):
            continue
    return None


def _filter_anachronistic_citations(
    groups: List[Dict[str, Any]], source_year: int
) -> List[Dict[str, Any]]:
    """Remove citations with publication dates after the source book's copyright year."""
    for group in groups:
        materials = group.get("Supplemental_Material", [])
        filtered = []
        for mat in materials:
            citation = mat.get("citation") or {}
            pub_date = citation.get("publication_date") or ""
            try:
                pub_year = int(str(pub_date)[:4])
            except (ValueError, IndexError):
                filtered.append(mat)
                continue
            if pub_year > source_year:
                logger.warning(
                    "Removing anachronistic citation: '%s' (%s) post-dates source (%d)",
                    citation.get("title", "unknown"),
                    pub_date,
                    source_year,
                )
            else:
                filtered.append(mat)
        group["Supplemental_Material"] = filtered
    return groups


def _has_document(material: Dict[str, Any]) -> bool:
    """True if the material carries an actual cited document (so the citation half of a
    hybrid is stored, but a pure-prose entry with an empty citation is NOT written as a
    bogus bibliography record). A document is present when the citation has a real title/
    author or an archive reference, OR a real verbatim_reference (hybrid prose+citation records
    keep their citation half regardless of prose_content)."""
    citation = material.get("citation") or {}
    if any(
        (citation.get(k) or "")
        for k in ("title", "author", "publisher")
        if str(citation.get(k) or "").strip().lower() not in ("", "unknown", "none")
    ):
        return True
    if material.get("archive_reference_number"):
        return True
    # A real verbatim_reference IS a document reference — store it REGARDLESS of prose_content.
    # The old gate required "verbatim AND NOT prose", so a HYBRID (prose + a cited document) dropped
    # its citation half (PATTERN 4). The prose half is routed to notes separately; the citation half
    # must still be stored. (A trivial fragment is excluded by a small length floor.)
    verbatim = (material.get("verbatim_reference") or "").strip()
    return len(verbatim) >= 8


def _route_one_material(
    material: Dict[str, Any],
    event_id: str,
    sub_event_id: str,
    sub_event_name: str,
    bib_dir: Path,
    book: str,
    chapter: str,
    grok_client: Any,
    enrich: bool,
    factual_items: list,
    ambiguous_items: list,
) -> int:
    """Route one supplemental material by content_class. Appends to ``factual_items`` /
    ``ambiguous_items`` in place; returns the bibliography-count delta (0 or 1)."""
    content_class = material.get("content_class", "ambiguous")

    # 3-class model (owner 2026-10-16) + legacy back-compat. A HYBRID is routed to
    # BOTH the bibliography (its citation) AND the notes-event (its prose).
    is_citation = content_class in ("citation", "document_reference", "hybrid")
    is_prose = content_class in ("prose", "factual_content", "hybrid")
    routed = False
    bib_delta = 0

    # CITATION half -> bibliography (Phase-3 resolves it). Skip if the citation is
    # empty (a pure prose entry carries no document to store).
    if is_citation and _has_document(material):
        if enrich:
            _enrich_material(material, grok_client)
        from src.extraction.bibliography import store_bibliography_entry

        store_bibliography_entry(bib_dir, material, book, chapter)
        bib_delta = 1
        routed = True

    # PROSE half -> Phase-2-style notes-event (entities extracted downstream).
    if is_prose:
        prose = (
            material.get("prose_content")
            or material.get("verbatim_reference", "")
            or ""
        )
        if prose.strip():
            factual_items.append(
                {
                    "verbatim_reference": prose,
                    "reference_type": material.get("reference_type", ""),
                    "reference_number": material.get("reference_number", ""),
                    "source_EventID": event_id,
                    "source_Sub-eventID": sub_event_id,
                    "source_Sub-event_Name": sub_event_name,
                    "BibliographyID": material.get("_BibliographyID"),
                }
            )
            routed = True

    # Human review when explicitly ambiguous OR when the material routed to
    # NEITHER bibliography nor notes-event (no-silent-drop: a bare cross-reference
    # / empty-citation-empty-prose entry must not vanish).
    if content_class == "ambiguous" or not routed:
        ambiguous_items.append(
            {
                "book": book,
                "chapter": chapter,
                "reference_type": material.get("reference_type", ""),
                "reference_number": material.get("reference_number", ""),
                "verbatim_reference": material.get("verbatim_reference", ""),
                "EventID": event_id,
                "Sub-eventID": sub_event_id,
            }
        )

    return bib_delta


def _route_by_content_class(
    all_supplemental: List[Dict[str, Any]],
    bib_dir: Path,
    book: str,
    chapter: str,
    grok_client: Any = None,
    enrich: bool = False,
) -> tuple:
    """Route materials by content_class. Returns (factual_items, ambiguous_items, bib_count)."""
    factual_items: list = []
    ambiguous_items: list = []
    bib_count = 0

    for sub_event_data in all_supplemental:
        event_id = sub_event_data.get("EventID", "")
        sub_event_id = sub_event_data.get("Sub-eventID", "")
        sub_event_name = sub_event_data.get("Sub-event_Name", "")

        for material in sub_event_data.get("Supplemental_Material", []):
            bib_count += _route_one_material(
                material,
                event_id,
                sub_event_id,
                sub_event_name,
                bib_dir,
                book,
                chapter,
                grok_client,
                enrich,
                factual_items,
                ambiguous_items,
            )

    return factual_items, ambiguous_items, bib_count


def _write_notes_event(
    event_file: Path,
    factual_items: List[Dict[str, Any]],
    endnote_urls: Optional[Dict[int, str]] = None,
) -> Optional[Path]:
    """Write factual content as a notes-event file alongside the source event."""
    if not factual_items:
        return None

    # Read metadata from source event file
    source_meta = {}
    try:
        with open(event_file, "r", encoding="utf-8") as f:
            source_data = json.load(f)
        source_event = source_data.get("Event", source_data)
        source_meta = {
            "book": source_event.get("book", ""),
            "author": source_event.get("author", ""),
            "series": source_event.get("series", ""),
        }
    except Exception as e:
        logger.warning("Supplemental extraction step failed: %s", e)

    urls = endnote_urls or {}
    notes_file = event_file.with_name(
        event_file.name.replace("-event.json", "-notes-event.json")
    )

    sub_events = []
    # Deterministic notes-event id: stable from book + chapter stem (section "notes" distinguishes
    # it from the main chapter event). Central helper -> idempotent across re-runs (no ripple).
    from src.utils.deterministic_ids import event_id as _event_id
    from src.utils.deterministic_ids import sub_event_id as _sub_event_id

    _chapter_stem = event_file.stem.replace("-event", "")
    _notes_eid = _event_id(source_meta.get("book", ""), _chapter_stem, "notes")
    for item_index, item in enumerate(factual_items):
        ref_num = item.get("reference_number")
        source_ref = {
            "reference_type": item["reference_type"],
            "reference_number": ref_num,
            "source_EventID": item["source_EventID"],
            "source_Sub-eventID": item["source_Sub-eventID"],
            "BibliographyID": item.get("BibliographyID"),
        }
        # Add source URL if available
        if ref_num and int(ref_num) in urls:
            source_ref["source_url"] = urls[int(ref_num)]
        # Stable sub-event id: parent + the item's own source reference + its ordinal (the ordinal
        # guarantees intra-parent uniqueness when two items share ref_num + source sub-event).
        _locator = f"ref:{ref_num}:{item.get('source_Sub-eventID', '')}:{item_index}"
        sub_events.append(
            {
                "Sub-eventID": _sub_event_id(_notes_eid, name=_locator),
                "Sub-event_summary": item["verbatim_reference"][:200],
                "Sub-event_fulltext": {"paragraph_1": item["verbatim_reference"]},
                "source_reference": source_ref,
                "Endnote_References": [],
                "Footnote_References": [],
            }
        )

    event_data = {
        "Event": {
            "EventID": _notes_eid,
            "Event_Name": f"Notes: {_chapter_stem}",
            "book": source_meta.get("book", ""),
            "author": source_meta.get("author", ""),
            "series": source_meta.get("series", ""),
            "Sub-events": sub_events,
        }
    }

    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(notes_file, event_data, entity="events")

    logger.info("Wrote %d factual items to %s", len(sub_events), notes_file.name)
    return notes_file


def _append_to_review_queue(bib_dir: Path, items: List[Dict[str, Any]]) -> None:
    """Append ambiguous items to review_queue.json."""
    if not items:
        return
    queue_file = bib_dir / "review_queue.json"
    # Locked read-modify-write: review_queue.json is a shared sink appended to by parallel event
    # workers; an unlocked read+extend+write lost-update would silently drop ambiguous items.
    from src.utils.file_lock import locked_json

    with locked_json(queue_file) as (data, save):
        existing = data if isinstance(data, list) else []
        existing.extend(items)
        save(existing)
    logger.info("Added %d ambiguous items to review queue", len(items))


def extract_supplemental(
    event_file: Path,
    grok_client: GrokClient,
    output_root: Path,
    enrich: bool = False,
) -> Optional[Path]:
    """Extract and classify endnotes/footnotes from event file.

    Routes document references to output/bibliography/,
    factual content to {chapter}-notes-event.json,
    and ambiguous items to review_queue.json.

    Args:
        enrich: If True, search online/offline repositories for bibliography URLs
                and determine copyright status before storing.
    """
    logger.info("Extracting supplemental material from %s", event_file.name)

    people_index = _build_people_index(output_root)
    groups_index = _build_groups_index(output_root)
    logger.debug(
        "Loaded %d people, %d groups for entity resolution",
        len(people_index),
        len(groups_index),
    )

    # Load event data
    event_data = _load_event_data(event_file)
    if not event_data:
        return None

    # Fetch actual endnote/footnote text from ibiblio
    from src.extraction.fetch_endnotes import fetch_endnote_texts

    parsed_file = event_file.with_name(
        event_file.name.replace("-event.json", "-parsed.json")
    )
    endnote_texts = fetch_endnote_texts(parsed_file)

    # OBSERV (bibliography chain): what does supplemental actually see for this event?
    _observe_supplemental_input(event_file, event_data, endnote_texts)

    # Create prompts
    prompts = create_supplemental_prompt(event_data, endnote_texts)
    if not prompts:
        logger.info("No sub-events found in %s", event_file.name)
        return None

    # Extract from API. Keep each response paired with its KNOWN parent ids so sanitize can
    # derive (not fabricate) any reference the LLM omitted — carried in a parallel list rather
    # than stashed on the dict (which would risk leaking into the validated record).
    all_supplemental = []
    known_parents = (
        []
    )  # parallel to all_supplemental: (known_event_id, known_sub_event_id)
    for sub_event_id, prompt, stable_material, parent_event_id in prompts:
        logger.debug("Processing sub-event %s", sub_event_id)
        response = _extract_with_retry(
            prompt,
            sub_event_id,
            grok_client,
            stable_key_material=stable_material,
            known_event_id=parent_event_id,
        )
        if response:
            all_supplemental.append(response)
            known_parents.append((parent_event_id, sub_event_id))

    # OBSERV: how many sub-event prompts returned materials (post-batch answers).
    _observe_supplemental_extract(event_file, prompts, all_supplemental)

    if not all_supplemental:
        logger.info("No supplemental material extracted from %s", event_file.name)
        return None

    # Sanitize and generate ULIDs
    for sub_event_data, (ev_id, se_id) in zip(all_supplemental, known_parents):
        sanitize_supplemental_data(
            sub_event_data, known_event_id=ev_id, known_sub_event_id=se_id
        )
    all_supplemental = generate_ulids(all_supplemental)

    # Route by content class
    book = event_file.parent.name
    chapter = event_file.name.replace("-event.json", "")
    bib_dir = output_root / "bibliography"

    factual, ambiguous, bib_count = _route_by_content_class(
        all_supplemental, bib_dir, book, chapter, grok_client, enrich
    )

    # Write factual content as notes-event file (URL map from the parsed file's footnotes).
    endnote_urls = _build_endnote_url_map(event_file)
    notes_file = _write_notes_event(event_file, factual, endnote_urls)

    # Queue ambiguous items for human review
    _append_to_review_queue(bib_dir, ambiguous)

    logger.info(
        "  %s: %d bibliography, %d factual, %d ambiguous",
        event_file.name,
        bib_count,
        len(factual),
        len(ambiguous),
    )

    return notes_file or (bib_dir if bib_count > 0 else None)


def _observe_supplemental_input(
    event_file: Path, event_data: Dict, endnote_texts: Dict
) -> None:
    """OBSERV: log what supplemental sees for this event (sub-events, ref counts, endnote texts).
    Observability must never break extraction, so all failures are swallowed."""
    try:
        subs = event_data.get("Event", {}).get("Sub-events", [])
        n_with_refs = sum(
            1
            for s in subs
            if isinstance(s, dict)
            and (s.get("Footnote_References") or s.get("Endnote_References"))
        )
        total_refs = sum(
            len(s.get("Footnote_References") or [])
            + len(s.get("Endnote_References") or [])
            for s in subs
            if isinstance(s, dict)
        )
        logger.info(
            "OBSERV supplemental.input file=%s sub_events=%d with_refs=%d total_refs=%d "
            "endnote_texts=%d",
            event_file.name,
            len(subs),
            n_with_refs,
            total_refs,
            len(endnote_texts),
        )
    except Exception:  # noqa: BLE001 - observability must never break extraction
        pass


def _content_class_histogram(all_supplemental: "list") -> Dict[str, int]:
    """Build a bounded, sanitized histogram of (Grok-derived) content_class labels: strip
    newlines (log-forging), truncate, and cap distinct keys so junk labels can't blow up the log.
    """
    classes: Dict[str, int] = {}
    for r in all_supplemental:
        for m in r.get("Supplemental_Material", []) or []:
            # Sanitize + bound the (Grok-derived) class label: strip newlines (log-forging),
            # truncate, cap distinct keys so junk labels can't blow up the log.
            c = (
                str((m or {}).get("content_class", "?"))
                .replace("\n", " ")
                .replace("\r", " ")[:40]
            )
            if c in classes or len(classes) < 50:
                classes[c] = classes.get(c, 0) + 1
    return classes


def _observe_supplemental_extract(
    event_file: Path, prompts: "list", all_supplemental: "list"
) -> None:
    """OBSERV: log post-batch materials + a bounded, sanitized content-class histogram."""
    try:
        mat_total = sum(
            len(r.get("Supplemental_Material", []) or [])
            for r in all_supplemental
            if isinstance(r, dict)
        )
        classes = _content_class_histogram(all_supplemental)
        dropped = max(0, len(prompts) - len(all_supplemental))
        logger.info(
            "OBSERV supplemental.extract file=%s prompts=%d responses=%d dropped=%d materials=%d "
            "content_classes=%s",
            event_file.name,
            len(prompts),
            len(all_supplemental),
            dropped,
            mat_total,
            classes,
        )
        # A dropped sub-event = _extract_with_retry returned None (validation error / exhausted
        # retries) and its supplemental/bibliography was silently discarded. Make it LOUD so a
        # prompt↔schema drift (the 2026-10-24 class) can't hide as a quiet gap between prompts and
        # responses.
        if dropped > 0:
            logger.warning(
                "OBSERV supplemental.anomaly file=%s: %d/%d sub-event(s) DROPPED "
                "(validation error / retries exhausted — bibliography data lost; check prompt↔schema)",
                event_file.name,
                dropped,
                len(prompts),
            )
    except Exception:  # noqa: BLE001 - observability must never break extraction
        pass


def _build_endnote_url_map(event_file: Path) -> Dict:
    """Build {footnote_number: url} from the event's sibling -parsed.json footnotes."""
    endnote_urls: Dict = {}
    try:
        parsed_file = event_file.with_name(
            event_file.name.replace("-event.json", "-parsed.json")
        )
        if parsed_file.exists():
            parsed_data = json.loads(parsed_file.read_text(encoding="utf-8"))
            for fn in parsed_data.get("footnotes", []):
                if fn.get("number") and fn.get("url"):
                    endnote_urls[fn["number"]] = fn["url"]
    except Exception as e:
        logger.warning("Supplemental extraction step failed: %s", e)
    return endnote_urls
