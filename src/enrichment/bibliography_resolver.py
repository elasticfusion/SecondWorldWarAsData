"""Bibliography source resolution — find actual documents for citations.

Routes bibliography entries by document type to appropriate search APIs:
  - Military records → NARA catalog (API key required) + Grok record group ID
  - Books → Archive.org, Gutenberg, OpenSERP
  - Journal articles → LOC, OpenSERP
  - Oral histories → LOC Veterans History Project

Each search result is verified by Grok before acceptance.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ulid

from src.utils.http_pool import get_session
from src.enrichment.local_holdings import build_holdings_index, find_local_holding

logger = logging.getLogger(__name__)

# Strip C0 controls (incl. \n AND \r) from any untrusted-at-rest text before it enters a
# log line — prevents OBSERV log-line forging (repo convention; matches grok_client).
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _looks_like_citation(entry: Dict[str, Any]) -> bool:
    """Heuristic: does this entry look like a bibliographic citation vs. narrative?

    Guards the NARA/online resolvers against narrative footnotes misclassified as
    document references (e.g. "In October 1941, the Germans had discussed..."),
    which waste identification calls. A citation is accepted if it carries any
    structured bibliographic signal (author, title, archive ref, RG number,
    date/page markers). Prose with none of these is treated as non-citation.
    """
    if _has_structured_citation_fields(entry):
        return True
    verbatim = _entry_verbatim(entry)
    if not verbatim.strip():
        # Nothing structured and no text → nothing to resolve; don't send to NARA.
        return False
    return _verbatim_looks_citational(verbatim)


def _has_structured_citation_fields(entry: Dict[str, Any]) -> bool:
    """True if the entry has any explicit bibliographic/archive field."""
    citation = entry.get("citation") or {}
    if any(
        _has_real_value(citation.get(k))
        for k in ("author", "title", "publisher", "record_group")
    ):
        return True
    return _has_real_value(entry.get("archive_reference_number"))


# Placeholder tokens that OCR/extraction leave in stub entries — these do NOT
# count as real bibliographic content (they'd otherwise pass a "has title" check
# and send stub entries to the NARA/online resolvers).
_PLACEHOLDER_VALUES = {"", "unknown", "none", "n/a", "na", "null", "-", "?"}


def _has_real_value(value: Any) -> bool:
    """True if a citation field holds real content (not empty/placeholder)."""
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return any(_has_real_value(v) for v in value)
    return str(value).strip().lower() not in _PLACEHOLDER_VALUES


# Citation-shaped signals: RG/box/folder, p./pp., a 4-digit year, "Last, First".
_STRUCT_PATTERNS = (
    r"\bRG\s*\d+",
    r"\b(?:pp?\.|p\.)\s*\d+",
    r"\b(?:box|folder|file|vol\.?|no\.?)\s*\d+",
    # --- Military / archival citation forms (US Army CMH "green book" footnotes). These
    # are unambiguously citational (archival document references) and are precisely the
    # NARA-resolvable ones, yet they carry none of the book-citation signals above — so
    # without these they were wrongly rejected as "not_citation" and NARA never ran.
    r"\bMS\s*#?\s*[A-Z]-?\d+",  # manuscript series, e.g. "MS # B-810"
    # military document-type abbreviations, each a strong citational signal
    r"\b(?:Ltr|Memo|Interv|Rpt|Jnl|Diary|Telecon|Telg|Msg|Cbl|Cable|Opns?|Per\s+Rpt|"
    r"AAR|FO|GO|SO|TWX|Ltr\s+of\s+Instrs?)\b",
    # unit + number designation (e.g. "VII Corps Opns Memo 101", "5th Armd Div G-2")
    r"\b(?:Corps|Div|Army|Gp|Regt|Bn|Co)\b.*\b(?:G-?\d|S-?\d|Memo|Rpt|Order|file)\b",
)
# Weak = corroborating, not establishing. A military short date ("12 Sep 44") appears
# PERVASIVELY in WWII narrative prose, so on its own it is NOT a citation (that caused
# ~36% false positives on prose) — it only counts toward the 2-signal threshold alongside
# another signal (a doc-type, a file ref, etc.).
_WEAK_PATTERNS = (
    r"\b(1[89]\d{2}|20\d{2})\b",
    r"[A-Z][a-z]+,\s+[A-Z]",
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",  # military short date
)


def _verbatim_looks_citational(verbatim: str) -> bool:
    """True if verbatim text carries citation structure (not just prose).

    Requires a strong structural signal (RG/box/page), OR at least two signals
    overall — so a prose sentence with only a stray year does not qualify.
    """
    strong = any(re.search(p, verbatim) for p in _STRUCT_PATTERNS)
    if strong:
        return True
    total = sum(
        1 for p in (_STRUCT_PATTERNS + _WEAK_PATTERNS) if re.search(p, verbatim)
    )
    return total >= 2


# Document types that are military/government records
ARCHIVE_TYPES = {
    "primary source",
    "primary source document",
    "after action report",
    "field order",
    "military message",
    "letter",
    "memo",
    "cable",
    "report",
    "journal",
    "war diary",
}

BOOK_TYPES = {"book", "monograph", "unit history"}
ARTICLE_TYPES = {"journal article", "periodical", "newspaper article"}


def _apply_local_holding(entry: Dict[str, Any], holdings: Any) -> bool:
    """Short-circuit to a locally-held source, if any. Returns True if the entry was resolved
    here (caller should return immediately). PROCESSED holdings -> resolved; raw UNPROCESSED
    staging files -> held_unprocessed (NOT a clean resolution)."""
    if not holdings:
        return False
    hit = find_local_holding(entry, holdings)
    if not hit:
        return False
    local_path, kind = hit
    # Use the schema-allowed field name (download_path). The prior name "local_source_path" is NOT
    # in BIBLIOGRAPHY_OUTPUT_SCHEMA (additionalProperties:false), so it was blocked at write time —
    # silently losing the resolution for every locally-held archival record.
    entry["download_path"] = local_path
    if kind == "processed":
        entry["search_status"] = "resolved"
        entry["search_source"] = "local_holding"
        entry["availability"] = "local"
        logger.debug("Resolved from processed local holding: %s", local_path)
    else:
        # Raw source on hand but not ingested — flag for the processing
        # queue rather than claiming resolution.
        entry["search_status"] = "held_unprocessed"
        entry["search_source"] = "local_holding_unprocessed"
        logger.debug("Held but unprocessed: %s", local_path)
    return True


def resolve_bibliography_entry(
    entry: Dict[str, Any],
    grok_client: Any = None,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Resolve a single bibliography entry to its source. Modifies in place."""
    # Short-circuit ONLY when we actually have an online copy (resource_urls). A prior run may
    # have set search_status="resolved" via NARA RG-IDENTIFY alone (archive_reference_number but
    # ZERO resource_urls) — that is a record-group LABEL, not a digitized copy, and must NOT
    # block the online-copy search (OpenSERP/archive.org/Gutenberg). Re-running Phase 3 should
    # therefore re-attempt those. A genuinely resolved entry carries resource_urls.
    if entry.get("resource_urls"):
        entry["search_status"] = "resolved"
        return entry

    # Best resolution: we ALREADY HOLD the cited source (e.g. MS # B-405 ->
    # B405.pdf). Short-circuit before any online search. Distinguish PROCESSED
    # holdings (ingested content exists -> resolved) from raw UNPROCESSED staging
    # files (we have the source but it still needs the pipeline -> held_unprocessed,
    # NOT a clean resolution).
    if _apply_local_holding(entry, (config or {}).get("holdings_index")):
        return entry

    # Guard: narrative prose misclassified as a document reference should not be
    # sent to NARA/online resolvers. Mark it distinctly (not "not_found", which
    # means "a real citation we couldn't locate") so it's filterable downstream.
    if not _looks_like_citation(entry):
        entry["search_status"] = "not_citation"
        logger.debug(
            "Skipping non-citation entry (narrative/prose, no bibliographic structure)"
        )
        return entry

    citation = entry.get("citation") or {}
    doc_type = (citation.get("document_type") or "").lower()
    availability = (entry.get("availability") or "").lower()
    cfg = config or {}

    # Green-book bibliographical note (authoritative, free): if this citation comes from a
    # HyperWar volume we cover, enrich from that volume's own Bibliographical Note before any
    # online search. A PUBLISHED WORK gets a canonical title/author (so the downstream
    # Archive.org/Gutenberg search can actually find a copy); an ARCHIVAL citation gets the
    # authoritative holding institution + provenance from the note.
    _enrich_from_biblio_note(entry, citation)

    # Deterministic bibliography records carry no document_type (title="Unknown"), so they
    # would default to the generic resolver (no NARA). Route by NARA-searchability of the
    # verbatim when the type is unknown, so archival refs reach _resolve_archive -> NARA.
    nara_ok = _is_nara_searchable(entry, _entry_verbatim(entry))
    resolver = _pick_resolver(doc_type, availability, nara_ok)
    result = resolver(entry, citation, grok_client, cfg)

    _apply_result(entry, result)
    return entry


def _enrich_from_biblio_note(entry: Dict[str, Any], citation: Dict[str, Any]) -> None:
    """Attach provenance from the source volume's HyperWar Bibliographical Note (if covered).
    Mutates entry/citation in place; fail-safe. Published works gain a canonical title/author
    that feeds the online-copy search; archival citations gain the holding institution.
    """
    try:
        from src.enrichment.hyperwar_biblio import resolve_from_biblio_note

        verbatim = _entry_verbatim(entry)
        source_url = ""
        for m in entry.get("mentions") or []:
            if m.get("source_url"):
                source_url = m["source_url"]
                break
        prov = resolve_from_biblio_note(verbatim, source_url)
        if not prov:
            return
        entry.setdefault("source_metadata", {}).update(prov)
        if prov.get("biblio_category") == "published_work":
            # Give the online-copy search a real, findable title/author.
            if not citation.get("alt_title"):
                citation["alt_title"] = prov["canonical_title"]
            if citation.get("title", "Unknown") in ("", "Unknown"):
                citation["title"] = prov["canonical_title"]
            if not citation.get("author"):
                citation["author"] = prov["canonical_author"]
            entry["citation"] = citation
        else:
            # Archival: record the authoritative holding as provenance (not an online copy).
            entry.setdefault("archive_physical_address", prov.get("holding", ""))
        logger.debug(
            "Biblio-note enrich: category=%s for %s",
            prov.get("biblio_category"),
            verbatim[:60],
        )
    except Exception as e:  # noqa: BLE001 - enrichment must never break resolution
        logger.debug("biblio-note enrich skipped: %s", e)
    return


def _entry_verbatim(entry: Dict[str, Any]) -> str:
    """The citation text: top-level verbatim_reference, else the first mention's (the
    deterministic/source-url split stores it on the mention)."""
    citation = entry.get("citation") or {}
    verbatim = str(entry.get("verbatim_reference") or citation.get("verbatim") or "")
    if verbatim.strip():
        return verbatim
    for m in entry.get("mentions") or []:
        mv = str(m.get("verbatim_reference") or "")
        if mv.strip():
            return mv
    return ""


def _pick_resolver(doc_type: str, availability: str, nara_searchable: bool = False):
    """Select resolver function by document type.

    ``nara_searchable`` lets an untyped-but-archival record (no document_type, e.g. a
    deterministic bibliography record) route to the archive/NARA resolver instead of the
    generic fallback that never queries NARA."""
    if doc_type in ARCHIVE_TYPES or availability == "archive":
        return _resolve_archive
    if doc_type in BOOK_TYPES or availability == "offline":
        return _resolve_book
    if doc_type in ARTICLE_TYPES:
        return _resolve_article
    if not doc_type and nara_searchable:
        return _resolve_archive
    return _resolve_generic_entry


def _apply_result(entry: Dict, result: Optional[Tuple]) -> None:
    """Apply resolver result to entry."""
    if not result:
        entry["search_status"] = "not_found"
        return
    url, source, extra = result
    if url:
        _apply_resolved_url(entry, url, source, extra)
    elif extra:
        entry.update(extra)
    # No online URL found. If the entry carries an archive_reference_number (already held, or
    # just added via extra), the RECORD GROUP is identified but there is no digitized copy ->
    # "reference_identified" (honest; a later run still retries the online sources). Only a
    # record with neither a URL nor an archive reference is genuinely "not_found".
    if not entry.get("resource_urls"):
        _sm = entry.get("source_metadata") or {}
        identified = bool(entry.get("archive_reference_number")) or bool(
            _sm.get("canonical_title")
        )
        entry["search_status"] = "reference_identified" if identified else "not_found"


def _apply_resolved_url(
    entry: Dict, url: str, source: str, extra: Optional[Dict]
) -> None:
    """Record a resolved online URL (+ optional PDF url from metadata) onto the entry and merge
    any additional source metadata. Sets availability/status to online/resolved."""
    entry.setdefault("resource_urls", []).append(url)
    # Add PDF URL from metadata if available
    if extra and extra.get("pdf_url"):
        pdf_url = extra.pop("pdf_url")
        if pdf_url not in entry.get("resource_urls", []):
            entry["resource_urls"].append(pdf_url)
    entry["search_source"] = source
    entry["availability"] = "online"
    entry["search_status"] = "resolved"
    # Merge additional metadata
    if extra:
        entry.setdefault("source_metadata", {}).update(extra)


def _resolve_archive(entry, citation, grok_client, config):
    """Resolve military/government records."""
    verbatim = entry.get("verbatim_reference", "")
    if not verbatim:
        mentions = entry.get("mentions") or []
        if mentions:
            verbatim = mentions[0].get("verbatim_reference", "")
    return _resolve_archive_record(entry, verbatim, grok_client, config)


def _resolve_book(entry, citation, grok_client, config):
    """Resolve books."""
    title = citation.get("title") or ""
    author = (citation.get("author") or [None])[0]
    result = _resolve_book_search(title, author, citation, grok_client, config)
    if not result:
        alt_title = citation.get("alt_title") or ""
        if alt_title and alt_title != title:
            result = _resolve_book_search(
                alt_title, author, citation, grok_client, config
            )
    return result


def _resolve_article(entry, citation, grok_client, config):
    """Resolve articles."""
    title = citation.get("title") or ""
    author = (citation.get("author") or [None])[0]
    result = _resolve_article_search(title, author, citation, grok_client, config)
    if not result:
        alt_title = citation.get("alt_title") or ""
        if alt_title and alt_title != title:
            result = _resolve_article_search(
                alt_title, author, citation, grok_client, config
            )
    return result


def _resolve_generic_entry(entry, citation, grok_client, config):
    """Generic fallback."""
    title = citation.get("title") or ""
    author = (citation.get("author") or [None])[0]
    result = _resolve_generic(title, author, grok_client, config)
    if not result:
        alt_title = citation.get("alt_title") or ""
        if alt_title and alt_title != title:
            result = _resolve_generic(alt_title, author, grok_client, config)
    return result


# --- Archive/Military Records ---


# Keywords indicating US or captured German military documents held at NARA
_NARA_INDICATORS = {
    "rg",
    "record group",
    "nara",
    "national archives",
    "ag file",
    "g-3",
    "g-2",
    "g-4",
    "shaef",
    "etousa",
    "fusa",
    "first army",
    "third army",
    "twelfth army",
    "army group",
    "after action",
    "aar",
    "field order",
    "operations order",
    "war diary",
    "jnl",
    "journal",
    "msg file",
    "cable",
    "ocmh",
    "wdss",
    "hist sec",
    "historical division",
    "ob west",
    "okw",
    "okh",
    "panzer",
    "armee",
    "heeresgruppe",
    "wehrmacht",
    "luftwaffe",
    "kriegstagebuch",
    " inf ",
    " div ",
    " fo ",
    "unit rpt",
    "unit report",
    "arty",
    "cav",
    "engr",
    "recon",
    "bn",
    "regt",
    # German Report Series / Foreign Military Studies (the "MS #/MS No" manuscripts) —
    # NARA RG 549 / OCMH. A major, individually-catalogued NARA holding.
    "ms #",
    "ms no",
    "ms. ",
    "ms b-",
    "ms p-",
    "ms t-",
    "ms d-",
    # Combat Interviews (NARA RG 407) — cited as "Interv", "Combat Interv".
    "interv",
    "combat interview",
    # Corps / operations shorthand pervasive in the green books (NARA RG 407 unit records).
    "corps",
    "opns",
    "operations memorandum",
    "opns memo",
    "memo",
    "ltr",
    "rpt",
    "msg",
    "sitrep",
    "overlay",
    "ftp file",
    "go ",
    "so ",
}


def _build_indicator_regex() -> "re.Pattern[str]":
    """Compile the indicators into one word-boundary alternation.

    Bare-substring matching mis-fired on short indicators ("rg" inside "argues", "bn"
    inside "urbane"). Each indicator is anchored so it only matches at token edges: a
    non-alnum (or string edge) must precede/follow. Manual space padding in the set (e.g.
    " inf ", "go ") is stripped since the boundary anchors now encode that intent.
    """
    alts = sorted(
        {ind.strip() for ind in _NARA_INDICATORS if ind.strip()}, key=len, reverse=True
    )
    body = "|".join(re.escape(a) for a in alts)
    return re.compile(rf"(?<![0-9a-z])(?:{body})(?![0-9a-z])", re.IGNORECASE)


_NARA_INDICATOR_RE = _build_indicator_regex()


def _nara_indicator_match(text: str) -> bool:
    """True if any NARA indicator appears as a bounded token in text."""
    return bool(_NARA_INDICATOR_RE.search(text))


def _is_nara_searchable(entry: Dict, verbatim: str) -> bool:
    """Check if this entry is likely a US/German military document at NARA."""
    # Already has a NARA record group reference
    ref = entry.get("archive_reference_number", "")
    if ref and ref != "None" and "RG" in ref.upper():
        return True
    # Check verbatim, title, and archive_ref for NARA indicators
    citation = entry.get("citation") or {}
    text = " ".join(
        [
            verbatim,
            citation.get("title", ""),
            ref if ref and ref != "None" else "",
        ]
    ).lower()
    if not text.strip():
        return False
    # Word-boundary match (not bare substring): a bare "rg" would otherwise match prose like
    # "argues"/"charge", and "bn"/"go" similarly. \b anchors each indicator to a token edge.
    if _nara_indicator_match(text):
        return True
    # Consistency with the citation detector: if the verbatim already carries military/
    # archival citation STRUCTURE (STRONG signal), it is a NARA candidate even if it uses a
    # shorthand not in the indicator list. Prevents a record that passed _looks_like_citation
    # from being silently dropped here before _search_nara ever runs.
    return _verbatim_looks_citational(verbatim)


def _resolve_archive_record(
    entry: Dict, verbatim: str, grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Resolve military/government records via Grok + NARA."""
    citation = entry.get("citation") or {}
    title = citation.get("title", "")
    search_text = verbatim or title

    extra = _identify_record_group(entry, search_text, grok_client)
    url = _search_online_sources(
        entry, verbatim, search_text, title, grok_client, config
    )
    if url:
        return (url[0], url[1], extra)
    return (None, "nara_grok", extra) if extra else None


def _identify_record_group(entry: Dict, search_text: str, grok_client: Any) -> Dict:
    """Ask Grok to identify NARA Record Group if not already known."""
    extra = {}
    current_ref = entry.get("archive_reference_number") or ""
    if current_ref == "None":
        current_ref = ""
    has_rg = "RG" in current_ref.upper() if current_ref else False

    if not has_rg and grok_client and search_text:
        ref = _identify_nara_record(search_text, grok_client)
        if ref:
            full_ref = f"{ref}, {current_ref}" if current_ref else ref
            extra["archive_reference_number"] = full_ref
            extra["archive_physical_address"] = (
                "National Archives and Records Administration (NARA), "
                "College Park, MD 20740, USA"
            )
    return extra


def _search_online_sources(
    entry: Dict,
    verbatim: str,
    search_text: str,
    title: str,
    grok_client: Any,
    config: Dict,
) -> Optional[Tuple[str, str]]:
    """Search NARA, OpenSERP, Archive.org, Gutenberg for online copies and AGGREGATE every
    verified finding (multiple findings per entry is expected/OK — a document can legitimately
    be available in several repositories). All verified (url, source) findings are written to
    ``entry['resource_urls']`` + ``entry['source_metadata']['findings']``; the FIRST finding is
    also returned as the primary ``(url, source)`` for backward-compat with ``_apply_result``.

    Every source uses the same NARA-style pattern — fetch candidates, Grok-verify each against
    the citation, keep the verified ones — rather than blindly taking a source's first hit.
    """
    findings: "list[Tuple[str, str]]" = []

    search_title, author, query = _online_search_params(entry, verbatim, title)

    # Each source uses the same pattern (fetch candidates -> Grok-verify -> keep verified),
    # extracted into its own helper so this aggregator stays flat. Order = source priority.
    findings += _find_in_nara(entry, verbatim, search_text, title, grok_client, config)
    findings += _find_in_openserp(entry, search_title, grok_client, config)
    findings += _find_in_archive_org(
        entry, query, search_title, author, grok_client, config
    )
    findings += _find_in_gutenberg(
        entry, query, search_title, author, grok_client, config
    )

    if not findings:
        return None
    _aggregate_findings(entry, findings)
    # Primary finding keeps the existing single-tuple contract (_apply_result appends it too,
    # which the dedup above already guards against).
    return findings[0]


def _online_search_params(
    entry: Dict, verbatim: str, title: str
) -> "Tuple[str, str, str]":
    """Compute (search_title, author, query) for the online-copy aggregator.

    Deterministic bibliography records carry title="Unknown" (real citation text lives on the
    mention/verbatim; Grok fills alt_title). search_title = Grok-expanded alt_title -> real
    title -> verbatim (never "Unknown"). author is comma-joined if a list. query = verbatim
    (capped) or the search_title fallback."""
    alt_title = (entry.get("citation") or {}).get("alt_title") or ""
    search_title = _pick_meaningful_title(alt_title, title, verbatim)
    author = _joined_author((entry.get("citation") or {}).get("author") or "")
    query = verbatim[:300] or search_title
    return search_title, author, query


def _joined_author(author: Any) -> str:
    """Comma-join a list author into a single string; pass through a non-list unchanged."""
    if isinstance(author, list):
        return ", ".join(a for a in author if a)
    return author


def _aggregate_findings(entry: Dict, findings: "list[Tuple[str, str]]") -> None:
    """Dedup-preserving-order the verified findings into entry['resource_urls'] + a structured
    entry['source_metadata']['findings'] list, and OBSERV the count/sources."""
    seen = set(entry.get("resource_urls") or [])
    for url, _source in findings:
        if url and url not in seen:
            entry.setdefault("resource_urls", []).append(url)
            seen.add(url)
    entry.setdefault("source_metadata", {}).setdefault("findings", []).extend(
        {"url": u, "source": s} for u, s in findings
    )
    logger.info(
        "OBSERV bib.findings count=%d sources=%s",
        len(findings),
        ",".join(sorted({s for _, s in findings})),
    )


def _find_in_nara(
    entry: Dict,
    verbatim: str,
    search_text: str,
    title: str,
    grok_client: Any,
    config: Dict,
) -> "list[Tuple[str, str]]":
    """NARA catalog — scoped by the DETERMINISTIC record group (not Grok) so the query is bounded
    to the right RG. Retries with the expanded alt_title. (_search_nara iterates + verifies.)
    """
    nara_key = config.get("nara_api_key")
    if not (nara_key and _is_nara_searchable(entry, verbatim)):
        return []
    rg = _lookup_record_group(verbatim or search_text or title)
    rg_num = rg[1] if rg else None
    query = verbatim[:100] or title
    url = _search_nara(
        query, nara_key, grok_client, verbatim, record_group_number=rg_num
    )
    if not url:
        url = _retry_nara_alt_title(
            entry, query, nara_key, grok_client, verbatim, rg_num
        )
    return [(url, "nara_catalog")] if url else []


def _retry_nara_alt_title(
    entry: Dict,
    query: str,
    nara_key: str,
    grok_client: Any,
    verbatim: str,
    rg_num: Optional[int],
) -> Optional[str]:
    """Retry the NARA catalog search with the Grok-expanded alt_title (when it differs from the
    first query). Returns the resolved URL or None."""
    alt_title = (entry.get("citation") or {}).get("alt_title") or ""
    if alt_title and alt_title != query:
        return _search_nara(
            alt_title[:100],
            nara_key,
            grok_client,
            verbatim,
            record_group_number=rg_num,
        )
    return None


def _find_in_openserp(
    entry: Dict, search_title: str, grok_client: Any, config: Dict
) -> "list[Tuple[str, str]]":
    """OpenSERP for digitized copies (HathiTrust, university sites). Iterates candidates across
    repository-targeted query templates + Grok-verifies each internally."""
    if not config.get("use_openserp"):
        return []
    openserp_url = config.get("openserp_url", "http://localhost:7001")
    current_ref = entry.get("archive_reference_number") or ""
    ref = current_ref if current_ref and current_ref != "None" else search_title
    url = _search_openserp_archive(ref, openserp_url, grok_client, entry)
    return [(url, "openserp_archive")] if url else []


def _find_in_archive_org(
    entry: Dict,
    query: str,
    search_title: str,
    author: str,
    grok_client: Any,
    config: Dict,
) -> "list[Tuple[str, str]]":
    """Archive.org — independent source (its own advancedsearch API), gated ONLY on its own flag.
    Iterates + verifies candidates via the shared _collect_verified_candidates helper.
    """
    if not config.get("search_archive_org", True):
        return []
    from src.extraction.supplemental_search import search_archive_org_candidates

    cands = search_archive_org_candidates(search_title, author or None)
    return [
        (url, "archive_org")
        for url in _collect_verified_candidates(query, cands, grok_client, entry)
    ]


def _find_in_gutenberg(
    entry: Dict,
    query: str,
    search_title: str,
    author: str,
    grok_client: Any,
    config: Dict,
) -> "list[Tuple[str, str]]":
    """Gutenberg — independent native source (Gutendex API), gated ONLY on its own flag. Same
    candidate-iterate-verify pattern; low hit expectation (literary texts, not WWII records).
    """
    if not config.get("search_gutenberg", True):
        return []
    from src.extraction.supplemental_search import search_gutenberg_candidates

    cands = search_gutenberg_candidates(search_title, author or None)
    return [
        (url, "gutenberg")
        for url in _collect_verified_candidates(query, cands, grok_client, entry)
    ]


def _collect_verified_candidates(
    query: str,
    candidates: "list[Tuple[str, str]]",
    grok_client: Any,
    entry: Optional[Dict] = None,
    max_findings: int = 3,
) -> "list[str]":
    """Shared NARA-style multi-finding core: iterate ``(url, title)`` candidates, Grok-verify
    each against ``query`` (via ``_verify_openserp_match``, the general citation-relevance
    check), and return UP TO ``max_findings`` verified URLs. Reused by the archive.org and
    Gutenberg paths so all sources share one candidate-iterate-verify implementation. When no
    grok_client is available, candidates are accepted unverified (test/offline mode)."""
    out: "list[str]" = []
    for url, cand_title in candidates:
        if not url:
            continue
        # Cheap deterministic pre-gate: if the candidate title shares NO meaningful token with
        # the citation, it cannot be the cited document — reject for FREE before spending a Grok
        # verify call. This is what keeps a garbage web-search result set ("The Unknown :
        # Flammarion", "Address Unknown", ...) from triggering dozens of serialized Grok calls.
        if grok_client and not _title_token_overlap(query, cand_title):
            logger.debug("  ✗ token-gate rejected: %s", cand_title[:60])
            continue
        if grok_client and not _verify_openserp_match(
            query, cand_title, url, grok_client, entry
        ):
            logger.debug("  ✗ rejected candidate: %s (%s)", cand_title[:60], url)
            continue
        out.append(url)
        if len(out) >= max_findings:
            break
    return out


_openserp_down = False


def _mark_backend_down(entry: Optional[Dict]) -> None:
    """Fail-safe: flag that an enrichment backend (OpenSERP) was unavailable while resolving this
    entry, so a resulting not_found is recorded as `backend_down` (a transient outage) rather than
    an authoritative "no copy exists". Never raises."""
    try:
        if isinstance(entry, dict):
            entry["_backend_down"] = True
    except (
        Exception
    ):  # noqa: BLE001 - observability tagging must never break resolution
        pass


def _search_openserp_archive(
    ref: str, openserp_url: str, grok_client: Any = None, entry: Dict = None
) -> Optional[str]:
    """Search OpenSERP for digitized copies of archive documents."""
    global _openserp_down
    if _openserp_down:
        # OpenSERP was disabled earlier this run (transient outage). A None here is NOT evidence
        # that no copy exists — tag the entry so a resulting not_found is recorded as
        # `backend_down`, never as an authoritative "no copy exists" absence.
        _mark_backend_down(entry)
        return None

    from src.utils.search_cache import cache_result, get_cached

    cached = get_cached("openserp_archive", ref)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    saw_200 = False  # bound before the try so a future non-returning except can't leave it unbound
    try:
        import time

        from src.utils.search_query_loader import render_search_queries

        # Broadened beyond a single 'digitized document' query: iterate repository-targeted
        # templates (HathiTrust, Internet Archive, university libraries, Google Books, general
        # full-text/pdf) so non-NARA digitized copies are found too.
        title, author = _openserp_search_title(entry, ref)
        queries = render_search_queries(
            "bibliography", "openserp", title=title, author=author
        )
        if not queries:
            queries = [f"{ref} digitized document"]

        session = get_session()
        url, saw_200 = _run_openserp_queries(
            session, openserp_url, queries, ref, grok_client, entry
        )
        if url:
            cache_result("openserp_archive", ref, url)
            logger.info("Found online copy: %s", url)
            return url
    except Exception as e:
        logger.warning("OpenSERP unreachable, disabling for this run: %s", e)
        _openserp_down = True
        _mark_backend_down(entry)
        return None

    # Only negative-cache a GENUINE miss (at least one query returned 200 with no usable result).
    # If every query was non-200 (e.g. 503 while OpenSERP scales 0->1), that's TRANSIENT — don't
    # poison the negative cache for 7d.
    if saw_200:
        cache_result("openserp_archive", ref, None)
    else:
        logger.info(
            "OBSERV bib.openserp_all_transient ref=%s (no 200, not negative-cached)",
            str(ref)[:60],
        )
    return None


def _run_openserp_queries(session, openserp_url, queries, ref, grok_client, entry):
    """Run the OpenSERP query templates (budgeted Grok-verify). Returns (first_verified_url_or_None,
    saw_200) where saw_200 is True iff at least one query actually returned HTTP 200 (so the caller
    can distinguish a genuine miss from an all-transient outage). Extracted to keep
    _search_openserp_archive below the C gate."""
    import time

    verify_budget = 5
    saw_200 = False
    for query in queries:
        if verify_budget <= 0:
            break
        time.sleep(5)
        from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

        resp = recorded_fetch(
            "openserp",
            f"{openserp_url}/mega/search",
            kind=TEXTUAL,
            method="GET",
            session=session,
            params={
                "text": query,
                "limit": "5",
                "mode": "any",
                "engines": "google,bing,duckduckgo",
            },
            timeout=15,
            result_summary_fn=lambda r: f"status={r.status_code}",
        )
        if resp.status_code != 200:
            continue
        saw_200 = True
        results = resp.json().get("results", [])
        url, verify_budget = _verify_openserp_results(
            query, results, ref, grok_client, entry, verify_budget
        )
        if url:
            return url, saw_200
    return None, saw_200


def _openserp_search_title(entry: Optional[Dict], ref: str) -> "Tuple[str, str]":
    """Pick a meaningful (title, author) for the OpenSERP query. NEVER the 'Unknown' placeholder
    (deterministic records' title) — prefer the Grok-expanded alt_title, then a real title, then
    the archive ref."""
    if not entry:
        return ref, ""
    citation = entry.get("citation") or {}
    title = _pick_meaningful_title(
        citation.get("alt_title") or "", citation.get("title") or "", ref
    )
    return title, (citation.get("author") or "")


def _pick_meaningful_title(alt: str, real_title: str, ref: str) -> str:
    """First non-empty, non-'Unknown' of (alt_title, real_title, ref); falls back to ref."""
    title = next((t for t in (alt, real_title, ref) if t and t != "Unknown"), ref)
    if not title or title == "Unknown":
        title = ref
    return title


def _verify_openserp_results(
    query: str,
    results: "list",
    ref: str,
    grok_client: Any,
    entry: Optional[Dict],
    verify_budget: int,
) -> "Tuple[Optional[str], int]":
    """Iterate one query's OpenSERP results: cheap token-gate, then a budgeted Grok verify.
    Returns (first_verified_url_or_None, remaining_budget)."""
    for r in results:
        if verify_budget <= 0:
            break
        url = r.get("url", "")
        if not url:
            continue
        result_title = r.get("title", "")
        # Cheap deterministic pre-gate before the Grok verify: a result sharing NO meaningful
        # token with the query is not the cited document — reject free.
        if grok_client and not _title_token_overlap(query, result_title):
            logger.debug("  ✗ OpenSERP token-gate: '%s'", result_title[:60])
            continue
        if grok_client:
            verify_budget -= 1  # a real Grok verify is about to be spent
        if grok_client and not _verify_openserp_match(
            ref, result_title, url, grok_client, entry
        ):
            logger.debug(
                "  ✗ OpenSERP rejected: '%s' for query '%s'",
                result_title[:60],
                query[:60],
            )
            continue
        return url, verify_budget
    return None, verify_budget


def _verify_openserp_match(
    query: str, result_title: str, url: str, grok_client: Any, entry: Dict = None
) -> bool:
    """Verify an OpenSERP result is relevant to the specific citation, not a random document.

    Equipment searches are allowed to match photographs, but must be of the correct equipment.
    All other searches must match the specific event/unit/document cited.
    """
    doc_type = ""
    if entry:
        citation = entry.get("citation") or {}
        doc_type = (citation.get("document_type") or "").lower()

    is_equipment = doc_type in {"equipment", "photograph", "image"}

    if is_equipment:
        verify_prompt = f"""Does this search result show an accurate photograph of the specific equipment cited?

Citation: "{query[:300]}"
Search result title: "{result_title[:300]}"
URL: {url}

Return ONLY "YES" or "NO"."""
    else:
        verify_prompt = f"""Does this search result match the specific document/event being searched for?

Citation searched: "{query[:300]}"
Search result title: "{result_title[:300]}"
URL: {url}

Return ONLY "YES" or "NO"."""

    from src.utils.prompt_loader import render_prompt

    prompt = render_prompt("bibliography_verify", verify_prompt=verify_prompt)

    try:
        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You verify whether search results match specific military document citations. Be strict.",
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_verify",
        )
        accepted = response.strip().upper().startswith("YES")
        logger.info(
            "Verify result: %s | query=%s | title=%s | url=%s",
            "ACCEPT" if accepted else "REJECT",
            query[:80],
            result_title[:80],
            url[:100],
        )
        return accepted
    except Exception:
        return False


# Authoritative Record Group lookup (NARA Record Group Explorer,
# https://www.archives.gov/findingaid/record-group-explorer). DETERMINISTIC — Grok is NOT
# used to determine record groups (its RG guesses are unreliable); Grok is limited to
# semantic work (alt_title expansion + match verification). Each entry maps a word-boundary
# citation signal to the authoritative (RG label, record-group number).
#
# Coverage spans all service branches (the project is not ground-army-only): Army ground,
# Army Air Forces, Navy, Marine Corps, Coast Guard, plus theater/allied/occupation HQ,
# strategic/intel, and seized foreign records. Order matters: SERVICE-SPECIFIC and
# distinctive signals first, generic Army unit-record signals LAST (so a naval or air
# "G-3"/journal is not mis-bucketed into RG 407). A miss returns None (unscoped, best-effort
# search) — never blocks the pipeline.
_RG_LOOKUP: List[Tuple["re.Pattern[str]", Tuple[str, int]]] = [
    # --- Foreign / seized / theater-specific (most distinctive) ---
    # German Report Series / Foreign Military Studies ("MS # B-810", "MS P-...").
    (
        re.compile(r"\bMS\s*#|\bMS\s+[A-Z]-\d|Foreign Military Studies", re.I),
        ("RG 549", 549),
    ),
    # Seized German/foreign records / Kriegstagebuch (war diary), OB WEST, OKW/OKH.
    (
        re.compile(r"Kriegstagebuch|\bKTB\b|OB\s+WEST|\bOKW\b|\bOKH\b", re.I),
        ("RG 242", 242),
    ),
    # --- Air (Army Air Forces / USAF) ---
    # Numbered/strategic air forces, bomb/fighter groups & squadrons, mission/combat reports.
    (
        re.compile(
            r"\bAAF\b|Army Air Force|\b(?:Eighth|Ninth|Twelfth|Fifteenth)\s+Air\s+Force"
            r"|\bBomb(?:ardment)?\s+(?:Gp|Group|Sqdn|Squadron)\b|Fighter\s+(?:Gp|Group|Sqdn)"
            r"|Mission Report|\bINTOPS\b",
            re.I,
        ),
        ("RG 18", 18),
    ),
    # USAF commands/activities/organizations (post-1947 + historical studies).
    (re.compile(r"\bUSAF\b|Air Force Historical|AFHRA", re.I), ("RG 342", 342)),
    # HQ U.S. Air Force / Air Staff.
    (
        re.compile(r"Air Staff|Hq\s+USAF|Headquarters,?\s+U\.?S\.? Air Force", re.I),
        ("RG 341", 341),
    ),
    # --- Navy (by subject matter) ---
    # Operating forces: war diaries/action reports of ships & task forces, COMINCH, fleets.
    (
        re.compile(
            r"\bCNO\b|Chief of Naval Operations|\bCOMINCH\b|War Diary,?\s+(?:USS|Task|Fleet)"
            r"|Action Report,?\s+USS|\bUSS\b|Task\s+(?:Force|Group)\s+\d|\bCTF\b"
            r"|Naval Operating Forces|\bComNav|Amphibious",
            re.I,
        ),
        ("RG 38", 38),
    ),
    # Naval Operating Forces command records (RG 313) — shore/fleet command establishments.
    (
        re.compile(r"Naval Operating Forces|Naval District|Shore Establishment", re.I),
        ("RG 313", 313),
    ),
    # Bureau of Naval Personnel (muster rolls, personnel, casualties).
    (re.compile(r"Bureau of Naval Personnel|BuPers|muster roll", re.I), ("RG 24", 24)),
    # Bureau of Ordnance (naval ordnance/weapons design).
    (re.compile(r"Bureau of Ordnance|BuOrd", re.I), ("RG 74", 74)),
    # Bureau of Yards and Docks (naval construction, Seabees facilities).
    (re.compile(r"Yards and Docks|BuDocks|Seabee", re.I), ("RG 71", 71)),
    # General records of the Department of the Navy / SecNav (1798-1947 = RG 80; 1947- = RG 428).
    (
        re.compile(
            r"Department of the Navy, 194[7-9]|194[7-9].*Department of the Navy", re.I
        ),
        ("RG 428", 428),
    ),
    (
        re.compile(r"Department of the Navy|SecNav|Secretary of the Navy", re.I),
        ("RG 80", 80),
    ),
    # Office of Naval Records & Library / ONI.
    (
        re.compile(
            r"Naval Records (?:and|&) Library|\bONI\b|Office of Naval Intelligence",
            re.I,
        ),
        ("RG 45", 45),
    ),
    # --- Marine Corps ---
    (
        re.compile(
            r"Marine\s+(?:Div|Division|Regt|Regiment|Bn|Battalion)|\bUSMC\b|Fleet Marine Force"
            r"|\bFMF\b|Marine Corps",
            re.I,
        ),
        ("RG 127", 127),
    ),
    # --- Coast Guard ---
    (re.compile(r"Coast Guard|\bUSCG\b", re.I), ("RG 26", 26)),
    # --- Theaters of war (by named theater in the subject matter) ---
    # Mediterranean Theater (MTOUSA, NATOUSA, Italy/North Africa campaigns).
    (
        re.compile(
            r"\bMTOUSA\b|\bNATOUSA\b|Mediterranean Theater|Fifth Army.*Italy|Italian Campaign",
            re.I,
        ),
        ("RG 492", 492),
    ),
    # China-Burma-India (CBI).
    (
        re.compile(r"\bCBI\b|China-Burma-India|China Burma India|Burma|Stilwell", re.I),
        ("RG 493", 493),
    ),
    # Middle Pacific / Pacific Ocean Areas (POA, CINCPAC/Nimitz area).
    (
        re.compile(
            r"Middle Pacific|Pacific Ocean Areas?|\bPOA\b|\bCINCPAC\b|Central Pacific",
            re.I,
        ),
        ("RG 494", 494),
    ),
    # Africa-Middle East Theater.
    (
        re.compile(
            r"Africa-Middle East|Middle East Theater|Persian Gulf Command", re.I
        ),
        ("RG 497", 497),
    ),
    # U.S. Army Forces in Alaska (Aleutians).
    (
        re.compile(r"Alaska Defense|U\.?S\.? Army.*Alaska|Aleutian", re.I),
        ("RG 547", 547),
    ),
    # Far East Command / SWPA / SCAP / GHQ (MacArthur Pacific HQ, occupation of Japan).
    (
        re.compile(
            r"\bSWPA\b|\bSCAP\b|Far East Command|\bGHQ\b.*(?:Pacific|Far East)|MacArthur HQ|USAFFE",
            re.I,
        ),
        ("RG 554", 554),
    ),
    # Generic "U.S. theaters of war, WWII" when a theater is implied but unspecified.
    (re.compile(r"Theater of War|Theaters of War", re.I), ("RG 332", 332)),
    # Army operational/tactical/support orgs (WWII & after) — armies/corps HQ as orgs.
    (
        re.compile(
            r"Army Ground Forces|\bAGF\b|Replacement.*Training|Operational.*Tactical",
            re.I,
        ),
        ("RG 338", 338),
    ),
    # --- Theater / Allied / occupation HQ (ETO) ---
    (
        re.compile(r"\bETOUSA\b|Hq\s+ETO|European Theater of Operations", re.I),
        ("RG 498", 498),
    ),
    (
        re.compile(r"\bSHAEF\b|Allied\s+Force|\bAFHQ\b|Allied Expeditionary", re.I),
        ("RG 331", 331),
    ),
    (
        re.compile(r"US Occupation|Military Government|\bUSFET\b|\bOMGUS\b", re.I),
        ("RG 260", 260),
    ),
    # --- Strategic / intelligence ---
    (re.compile(r"Strategic Bombing Survey|\bUSSBS\b", re.I), ("RG 243", 243)),
    (re.compile(r"\bOSS\b|Office of Strategic Services", re.I), ("RG 226", 226)),
    # --- High command / staff ---
    # Joint Chiefs of Staff.
    (
        re.compile(r"\bJCS\b|Joint Chiefs of Staff|Combined Chiefs|\bCCS\b", re.I),
        ("RG 218", 218),
    ),
    # Office of the Secretary of Defense (post-1947).
    (re.compile(r"Secretary of Defense|\bOSD\b", re.I), ("RG 330", 330)),
    # Office of the Secretary of War.
    (re.compile(r"Secretary of War|\bOSW\b|Stimson", re.I), ("RG 107", 107)),
    # Army Staff (post-1947 successor to WDGS).
    (
        re.compile(r"Army Staff|\bGSUSA\b|Chief of Staff.*Army, 194[7-9]", re.I),
        ("RG 319", 319),
    ),
    # War Department General & Special Staffs (WWII G-2/OPD at War-Dept level).
    (
        re.compile(
            r"\bWDGS\b|War Department General|\bOPD\b|Operations Division.*War Department",
            re.I,
        ),
        ("RG 165", 165),
    ),
    # --- Army technical services / bureaus (by service in the subject matter) ---
    # Chief Signal Officer (signals, communications, photography).
    (re.compile(r"Chief Signal Officer|Signal Corps|\bSigC\b", re.I), ("RG 111", 111)),
    # Chief of Ordnance (Army ordnance/materiel development).
    (
        re.compile(r"Chief of Ordnance|Ordnance Department|Ordnance Dept", re.I),
        ("RG 156", 156),
    ),
    # Chemical Warfare Service.
    (re.compile(r"Chemical Warfare|\bCWS\b", re.I), ("RG 175", 175)),
    # Surgeon General (Army) — medical.
    (
        re.compile(r"Surgeon General|Medical Department.*Army|Army Medical", re.I),
        ("RG 112", 112),
    ),
    # Judge Advocate General (Army) — courts-martial, war crimes trials.
    (
        re.compile(
            r"Judge Advocate General|\bJAG\b|court-martial|courts-martial", re.I
        ),
        ("RG 153", 153),
    ),
    # Inspector General (Army).
    (re.compile(r"Inspector General", re.I), ("RG 159", 159)),
    # --- Army commands/units (continental & regular-army mobile) ---
    (
        re.compile(
            r"Continental Command|Army Service Forces|\bASF\b|Service Command", re.I
        ),
        ("RG 393", 393),
    ),
    (
        re.compile(
            r"Regular Army.*(?:Regiment|Mobile)|coast artillery|\bRA\b regiment", re.I
        ),
        ("RG 391", 391),
    ),
    # --- State / diplomatic / other agencies (by subject) ---
    (
        re.compile(r"Department of State|State Department|\bFRUS\b|diplomatic", re.I),
        ("RG 59", 59),
    ),
    (
        re.compile(r"Foreign Service Post|Embassy|Consulate|Legation", re.I),
        ("RG 84", 84),
    ),
    (
        re.compile(
            r"War Relocation Authority|\bWRA\b|Japanese.*internment|relocation center",
            re.I,
        ),
        ("RG 210", 210),
    ),
    (re.compile(r"Panama Canal", re.I), ("RG 185", 185)),
    (
        re.compile(r"Defense Mapping|Army Map Service|\bAMS\b map|topographic", re.I),
        ("RG 456", 456),
    ),
    (
        re.compile(
            r"Manhattan (?:Project|Engineer)|\bMED\b.*atomic|Department of Energy", re.I
        ),
        ("RG 434", 434),
    ),
    (re.compile(r"National War Labor Board|War Labor Board", re.I), ("RG 202", 202)),
    # --- Army ground unit records (GENERIC — must stay LAST so everything above wins) ---
    # AAR, G-1..G-4 journals, periodic reports, field orders, corps/division operational
    # records -> Adjutant General's Office.
    (
        re.compile(
            r"\bAAR\b|\bJnl\b|Per\s+Rpt|\bG-[1-4]\b|\bFO\b|\bOpns?\b|Corps|\bDiv\b"
            r"|Field Order|After Action|Journal",
            re.I,
        ),
        ("RG 407", 407),
    ),
]


def _lookup_record_group(verbatim: str) -> Optional[Tuple[str, int]]:
    """Deterministic (RG label, RG number) for a citation, via the authoritative Record
    Group Explorer alias map; None if no confident match. NO LLM."""
    text = verbatim or ""
    for pattern, rg in _RG_LOOKUP:
        if pattern.search(text):
            return rg
    return None


def _identify_nara_record(verbatim: str, grok_client: Any = None) -> Optional[str]:
    """The NARA Record Group for a citation — DETERMINISTIC lookup (not Grok).

    Returns the bare 'RG N' label (e.g. 'RG 407'), never a free-form rationale.
    ``grok_client`` is accepted for signature compatibility but unused: record-group
    determination is a lookup against NARA's authoritative Record Group Explorer, not a
    semantic judgement. Fail-safe: a miss returns None and the pipeline continues."""
    rg = _lookup_record_group(verbatim)
    if rg:
        logger.info(
            "NARA identify: %s (deterministic) | citation=%s", rg[0], verbatim[:80]
        )
        return rg[0]
    logger.info("NARA identify: NOT FOUND (no RG alias) | citation=%s", verbatim[:80])
    return None


# NARA's q parser treats citation punctuation (#, commas, trailing periods) as an
# *invalid query* and then returns 200 OK with the single-page-app HTML shell instead
# of a JSON error (documented failure mode). Sanitize to plain keywords: keep letters,
# digits and spaces, collapse whitespace. Quote/operator syntax is deliberately dropped
# because the raw citation is not a hand-crafted boolean query.
_NARA_Q_STRIP = re.compile(r"[^0-9A-Za-z\u00c0-\u024f ]+")


def _sanitize_nara_query(query: str) -> str:
    """Reduce a raw citation to a NARA-safe keyword query (avoids the 200-HTML shell)."""
    cleaned = _NARA_Q_STRIP.sub(" ", query or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _is_html_shell(resp: Any) -> bool:
    """True if NARA returned its SPA HTML shell (its documented invalid-query / rate-limit
    failure mode: 200 OK + text/html), rather than a JSON API response."""
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "html" in ctype:
        return True
    # Secondary sniff (content-type may lie): widen the window so a BOM, XML prolog, or
    # leading comment before the SPA's <!doctype/<html> is still caught.
    body = (resp.text or "").lstrip().lower()[:256]
    return "<!doctype" in body or "<html" in body


def _extract_na_id(hit: Dict[str, Any]) -> str:
    """Pull the naId from a search hit across the known response shapes.

    Per the API docs the record lives at _source.record and the naId may appear as the
    hit _id, record.naId, or metadata.controlGroup.naId."""
    source = hit.get("_source", {}) or {}
    record = source.get("record", source) or {}
    control = (source.get("metadata", {}) or {}).get("controlGroup", {}) or {}
    for candidate in (
        hit.get("_id"),
        record.get("naId"),
        source.get("naId"),
        control.get("naId"),
    ):
        if candidate:
            return str(candidate)
    return ""


def _nara_digital_object_count(record: Dict[str, Any]) -> int:
    """How many digitized objects (page scans) are attached to a NARA record. >0 means the
    record is ALREADY ONLINE. We deliberately COUNT rather than return the object URLs: an
    objectUrl is a single PAGE image (e.g. '...A3840-02-0001.tif') — a multi-page document
    would be misrepresented by page 1 and its other pages missed. The resource we surface
    is the catalog RECORD page (/id/<naId>), which presents ALL pages + the description.
    """
    n = 0
    for obj in record.get("digitalObjects") or []:
        if isinstance(obj, dict) and obj.get("objectUrl"):
            n += 1
    return n


_MATCH_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "from",
        "see",
        "also",
        "cf",
        "no",
        "nos",
        "vol",
        "vols",
        "pt",
        "fn",
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "january",
        "february",
        "march",
        "april",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        # generic archival / military filler that appears everywhere -> not discriminating
        "records",
        "record",
        "report",
        "reports",
        "file",
        "files",
        "collection",
        "series",
        "world",
        "war",
        "wwii",
        "army",
        "military",
        "united",
        "states",
        "office",
        "general",
        "headquarters",
        "misc",
        "miscellaneous",
        "papers",
        "document",
        "documents",
        "folder",
        "box",
        "level",
        "scope",
        "path",
        "fileunit",
        # Placeholder + junk-web-result tokens: 'unknown' is the deterministic-record title
        # placeholder (leaks into queries + matches junk like "Address Unknown"); the rest are
        # generic web-result noise. Excluding them stops false token-overlap on garbage results.
        "unknown",
        "pdf",
        "download",
        "free",
        "ebook",
        "online",
    }
)


def _match_tokens(text: str) -> set:
    """Lowercase content tokens (>=3 chars, not a stopword/pure number) for overlap check."""
    out = set()
    for tok in re.findall(r"[A-Za-z0-9]+", (text or "").lower()):
        if len(tok) >= 3 and tok not in _MATCH_STOPWORDS and not tok.isdigit():
            out.add(tok)
    return out


def _title_token_overlap(citation: str, candidate: str) -> bool:
    """True if the citation and the candidate record share >=1 MEANINGFUL token. A cheap
    deterministic relevance pre-gate: no shared content word -> cannot be the cited
    document, so reject before spending a Grok verify call. Fail-safe: if either side has
    NO meaningful tokens (can't judge), return True so Grok still decides."""
    c = _match_tokens(citation)
    d = _match_tokens(candidate)
    if not c or not d:
        return True
    return bool(c & d)


def _nara_match_context(record: Dict[str, Any]) -> str:
    """A compact context string for match verification: the series/ancestors path, the
    level of description, and the scope note. A green-book citation matches at the
    series/unit level, so this context (e.g. 'World War II Operations Reports') is what
    lets the verifier recognize a correct record behind a cryptic file-unit title."""
    parts: List[str] = []
    lod = record.get("levelOfDescription")
    if lod:
        parts.append(f"level: {lod}")
    anc = record.get("ancestors")
    if isinstance(anc, list):
        names = [
            a.get("title", "") for a in anc if isinstance(a, dict) and a.get("title")
        ]
        if names:
            parts.append("series path: " + " > ".join(names[:5]))
    scope = record.get("scopeAndContentNote") or record.get("scopeContentNote") or ""
    if scope:
        parts.append("scope: " + str(scope)[:300])
    return " | ".join(parts)


def _interpret_nara_response(resp, safe_q: str) -> Optional[Dict]:
    """Interpret a NARA search HTTP response into a parsed payload, or None for every non-usable
    outcome (429 rate-limit w/ 60s backoff, non-200, the documented 200+HTML-shell invalid-query
    mode, json-parse failure). None is NOT negative-cached by the caller so a fixed query/retry can
    still succeed next run."""
    import time

    if resp.status_code == 429:
        logger.warning("NARA API rate limited (429) — backing off 60s")
        time.sleep(60)
        return None  # Don't cache — retry next run
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0]
    if resp.status_code != 200:
        logger.info(
            "OBSERV nara.query status=%d content_type=%s q=%r",
            resp.status_code,
            ctype,
            safe_q[:60],
        )
        return None
    # NARA's documented failure mode: 200 OK + HTML SPA shell on an invalid query or rate-limit.
    # NOT a real not_found — surface distinctly, do NOT negative-cache.
    if _is_html_shell(resp):
        logger.warning(
            "OBSERV nara.query html_shell=1 status=200 content_type=%s q=%r "
            "(invalid-query/rate-limit shell, not a real not_found)",
            ctype,
            safe_q[:60],
        )
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning(
            "OBSERV nara.query json_parse_error=1 content_type=%s q=%r",
            ctype,
            safe_q[:60],
        )
        return None


def _search_nara(
    query: str,
    api_key: str,
    grok_client: Any,
    verbatim: str,
    record_group_number: Optional[int] = None,
) -> Optional[str]:
    """Search NARA catalog API v2.

    When ``record_group_number`` is known (deterministic RG lookup), the search is scoped
    with ``recordGroupNumber=N`` so the query is bounded to the right record group instead
    of matching tens of thousands of unrelated records (which defeats verification)."""
    from src.utils.search_cache import cache_result, get_cached

    # Cache is keyed on the RAW query + RG scope (so scoped vs unscoped don't collide). If
    # the sanitizer changes, bump intentionally — stale negatives key on the raw input.
    cache_q = f"{query}|rg={record_group_number}" if record_group_number else query
    cached = get_cached("nara", cache_q)
    if cached == "NOT_FOUND":
        return None
    if cached:
        return cached

    _nara_rate_limit()

    try:
        resolved_url, transient = _execute_nara_query(
            query, api_key, grok_client, verbatim, record_group_number, cache_q
        )
        if resolved_url:
            cache_result("nara", cache_q, resolved_url)
            return resolved_url
        # Negative-cache ONLY a genuine not-found. A transient outcome (429 / non-200 /
        # HTML-shell invalid-query / JSON-parse error — all signalled by transient=True) must
        # NOT be negative-cached, so a retry can still succeed next run.
        if not transient:
            cache_result("nara", cache_q, None)
        return None
    except Exception as e:
        logger.debug("NARA search failed: %s", e)
        cache_result("nara", cache_q, None)
        return None


def _nara_rate_limit() -> None:
    """Throttle NARA calls to ~10/min via a function-attribute last-call timestamp on
    _search_nara (behaviour identical to the prior inline block)."""
    import time

    if not hasattr(_search_nara, "_last_call"):
        _search_nara._last_call = 0.0  # type: ignore[attr-defined]
    elapsed = time.time() - _search_nara._last_call  # type: ignore[attr-defined]
    if elapsed < 6:  # Max ~10/min
        time.sleep(6 - elapsed)
    _search_nara._last_call = time.time()  # type: ignore[attr-defined]


def _execute_nara_query(
    query: str,
    api_key: str,
    grok_client: Any,
    verbatim: str,
    record_group_number: Optional[int],
    cache_q: str,
) -> Tuple[Optional[str], bool]:
    """Issue the NARA catalog HTTP request, interpret the response, OBSERV-log, and verify hits.
    Returns ``(resolved_url, transient)``: ``resolved_url`` is the catalog URL or None, and
    ``transient`` is True when the None result is a TRANSIENT/invalid-query outcome (429 /
    non-200 / HTML-shell / JSON-parse error) that the caller must NOT negative-cache. An empty-
    after-sanitize query is a genuine (non-transient) no-result and returns (None, False).
    """
    session = get_session()
    safe_q = _sanitize_nara_query(query)
    if not safe_q:
        logger.info(
            "OBSERV nara.query skipped=empty_after_sanitize raw=%r",
            _CTRL.sub(" ", query or "")[:60],
        )
        return None, False
    params: Dict[str, str] = {"q": safe_q, "limit": "5"}
    if record_group_number:
        params["recordGroupNumber"] = str(record_group_number)
    from src.utils.fetch_provenance import TEXTUAL, recorded_fetch

    resp = recorded_fetch(
        "nara",
        "https://catalog.archives.gov/api/v2/records/search",
        kind=TEXTUAL,
        method="GET",
        session=session,
        params=params,
        headers={"x-api-key": api_key, "Accept": "application/json"},
        timeout=30,
        result_summary_fn=lambda r: f"nara-search status={r.status_code}",
    )
    payload = _interpret_nara_response(resp, safe_q)
    if payload is None:
        # Transient / invalid-query — do NOT negative-cache (retry can succeed next run).
        return None, True
    results = payload.get("body", {}).get("hits", {}).get("hits", [])
    total = payload.get("body", {}).get("hits", {}).get("total", {})
    total_val = total.get("value") if isinstance(total, dict) else total
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0]
    logger.info(
        "OBSERV nara.query status=200 content_type=%s hits=%s returned=%d q=%r",
        ctype,
        total_val,
        len(results),
        safe_q[:60],
    )
    return _verify_nara_hits(results, query, verbatim, safe_q, grok_client)


def _verify_nara_hits(
    results: "list",
    query: str,
    verbatim: str,
    safe_q: str,
    grok_client: Any,
) -> Tuple[Optional[str], bool]:
    """Iterate NARA search hits: cheap token-gate, then a Grok relevance verify, returning the
    catalog RECORD page (/id/<naId>) of the first accepted hit (NOT a page-level objectUrl — the
    /id page presents ALL pages + the description). Returns ``(url, False)`` on a verified match
    and ``(None, False)`` when nothing verifies (a genuine not-found — non-transient).
    """
    for hit in results:
        source = hit.get("_source", {})
        record = source.get("record", source)
        title = record.get("title", source.get("title", ""))
        nara_id = _extract_na_id(hit)
        logger.debug("  Candidate: %s (naId=%s)", title[:80], nara_id)
        if not nara_id:
            continue
        url = f"https://catalog.archives.gov/id/{nara_id}"
        # Give the verifier the record's CONTEXT (series/ancestors path + scope note + level) —
        # a green-book citation matches at the series/unit level, and the cryptic file-unit title
        # alone (e.g. "XIV Corps: 4-004") is not enough for the LLM to judge.
        nara_context = _nara_match_context(record)
        # Deterministic pre-gate before spending a Grok call: the citation and the candidate
        # (title + context) must share at least one MEANINGFUL token — else it cannot be the
        # cited document, reject cheaply.
        if not _title_token_overlap(verbatim or query, f"{title} {nara_context}"):
            logger.debug(
                "  ✗ Rejected (no token overlap): '%s' vs query '%s'",
                title[:60],
                (verbatim or query)[:60],
            )
            continue
        if grok_client and not _verify_nara_match(
            query, title, grok_client, context=nara_context
        ):
            logger.debug(
                "  ✗ Rejected: '%s' does not match query '%s'", title[:60], query[:60]
            )
            continue
        logger.debug("  ✓ Accepted: %s", url)
        # Record whether the matched record is ALREADY ONLINE + its page-scan count, but ALWAYS
        # return the /id record page (a .tif objectUrl is a single page; a multi-page document
        # would be misrepresented by page 1 and its other pages missed).
        n_objs = _nara_digital_object_count(record)
        logger.info(
            "OBSERV nara.match naId=%s online=%d digital_objects=%d q=%r",
            nara_id,
            1 if n_objs else 0,
            n_objs,
            safe_q[:50],
        )
        return url, False
    return None, False


# --- Books ---


def _resolve_book_search(
    title: str, author: Optional[str], citation: Dict, grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Resolve books via Archive.org, Gutenberg, OpenSERP."""
    from src.extraction.supplemental_search import (
        search_archive_org,
        search_gutenberg_openserp,
        search_openserp,
    )

    # Archive.org
    url = search_archive_org(title, author)
    if url and _verify_match(title, url, grok_client):
        # Fetch metadata (PDF link, OCR info, page count)
        from src.extraction.supplemental_search import fetch_archive_org_metadata

        identifier = url.rstrip("/").split("/")[-1]
        meta = fetch_archive_org_metadata(identifier)
        return (url, "archive_org", meta)

    # Gutenberg
    openserp_url = config.get("openserp_url", "http://localhost:7001")
    if config.get("search_gutenberg", True):
        url = search_gutenberg_openserp(title, author, openserp_url)
        if url and _verify_match(title, url, grok_client):
            return (url, "gutenberg", None)

    # OpenSERP general
    if config.get("use_openserp", False):
        url = search_openserp(title, author, openserp_url)
        if url and _verify_match(title, url, grok_client):
            return (url, "openserp", None)

    return None


# --- Articles ---


def _resolve_article_search(
    title: str, author: Optional[str], citation: Dict, grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Resolve journal articles."""
    from src.extraction.supplemental_search import search_archive_org

    url = search_archive_org(title, author)
    if url and _verify_match(title, url, grok_client):
        return (url, "archive_org", None)
    return None


# --- Generic ---


def _resolve_generic(
    title: str, author: Optional[str], grok_client: Any, config: Dict
) -> Optional[Tuple[Optional[str], str, Optional[Dict]]]:
    """Generic search fallback."""
    from src.extraction.supplemental_search import search_archive_org

    url = search_archive_org(title, author)
    if url:
        return (url, "archive_org", None)
    return None


def _verify_nara_match(
    query: str, nara_title: str, grok_client: Any, context: str = ""
) -> bool:
    """Verify a NARA catalog result actually matches the searched citation.

    Checks that the specific unit, document type, and time period align, using the record's
    series/ancestors CONTEXT (not just the terse file-unit title) so a correct series-level
    match is recognized. A generic WWII document from the wrong unit is NOT a match.
    """
    from src.utils.prompt_loader import render_prompt as _rp

    prompt = _rp(
        "nara_verify", citation=query, nara_title=nara_title, nara_description=context
    )

    try:
        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You verify whether NARA catalog records match specific military document citations. Be strict about unit identity.",
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_verify",
        )
        return response.strip().upper().startswith("YES")
    except Exception:
        return False  # Reject on failure rather than accept wrong documents


# --- Verification ---


def _verify_match(candidate_title: str, verbatim: str, grok_client: Any) -> bool:
    """Use Grok to verify a search result matches the citation (title + content check).

    Args:
        candidate_title: The title of the cited work being searched for.
        verbatim: The URL found by search, or the original citation text.
        grok_client: Grok API client for verification.
    """
    if not grok_client:
        return True

    # Determine if verbatim is a URL (from search) or citation text
    url = verbatim if verbatim.startswith("http") else None
    citation_text = candidate_title

    # Quick title-based check (only if we don't have a URL to verify)
    if not url:
        verify_prompt = f"""Does this search result match the citation?

Citation: "{candidate_title[:200]}"
Result: "{verbatim[:200]}"

Return ONLY "YES" or "NO"."""
        from src.utils.prompt_loader import render_prompt as _rp2

        prompt = _rp2("bibliography_verify", verify_prompt=verify_prompt)
        try:
            response = grok_client.chat_completion(
                prompt=prompt,
                system_prompt="You verify whether search results match citations.",
                temperature=0.0,
                use_cache=True,
                cache_type="bibliography_verify",
            )
            accepted = response.strip().upper().startswith("YES")
            logger.info(
                "Verify title: %s | citation=%s | result=%s",
                "ACCEPT" if accepted else "REJECT",
                candidate_title[:80],
                verbatim[:80],
            )
            return accepted
        except Exception:
            return True

    # URL found — verify page content matches the citation
    return _verify_url_content(url, citation_text, grok_client)


def _verify_url_content(url: str, citation: str, grok_client: Any) -> bool:
    """Fetch first ~2000 chars of a URL and ask Grok if it matches the citation.

    The URL is UNTRUSTED (a web-search / LLM-returned result), so the fetch is routed through
    fetch_text_safe (SSRF-safe: clean_url + DNS-rebinding resolve-check + no redirects + byte cap)
    rather than a raw session.get. A URL BLOCKED by the SSRF guard is a hard REJECT (not the lenient
    accept-on-transient-failure below) — an unfetchable/blocked URL can't be verified as a match.
    """
    from src.utils.url_hygiene import fetch_text_safe, clean_url

    if clean_url(url) is None:
        logger.warning(
            "OBSERV bib.verify_url_blocked ssrf=1 url=%s citation=%s",
            _CTRL.sub(" ", url)[:100],
            _CTRL.sub(" ", citation)[:80],
        )
        return False  # unsafe/malformed host -> cannot verify -> reject
    try:
        text, status = fetch_text_safe(url, timeout=10)
        if status != 200 or text is None:
            # status is None when the SSRF guard blocked the host at resolve time (DNS-rebind) —
            # distinguish that from an ordinary non-200 so a blocked host is a visible reject.
            if status is None:
                logger.warning(
                    "OBSERV bib.verify_url_blocked ssrf=1 resolve=1 url=%s",
                    _CTRL.sub(" ", url)[:100],
                )
            return False

        # Extract text content (strip HTML if needed)
        content = text[:4000]
        if "<html" in content.lower():
            from html2text import html2text

            content = html2text(content)[:2000]
        else:
            content = content[:2000]

        verify_prompt = f"""Does this web page content match the cited document?

Citation: "{citation[:300]}"

Page content (first 2000 chars):
{content}

Return ONLY "YES" or "NO". Answer "NO" if the page is unrelated, a generic search page, a paywall, or doesn't contain the cited document."""
        from src.utils.prompt_loader import render_prompt as _rp3

        prompt = _rp3("bibliography_verify", verify_prompt=verify_prompt)

        response = grok_client.chat_completion(
            prompt=prompt,
            system_prompt="You verify whether web page content matches a bibliography citation.",
            temperature=0.0,
            use_cache=True,
            cache_type="bibliography_verify",
        )
        match = response.strip().upper().startswith("YES")
        logger.info(
            "Verify URL content: %s | url=%s | citation=%s",
            "ACCEPT" if match else "REJECT",
            url[:100],
            citation[:80],
        )
        return match
    except Exception as e:
        logger.debug("URL content verification failed for %s: %s", url[:80], e)
        return True  # Accept on transient failure to avoid blocking


# --- Batch Processing ---


_REUSE_FIELDS = (
    "search_status",
    "resource_urls",
    "search_source",
    "availability",
    "archive_reference_number",
    "archive_physical_address",
    "source_metadata",
)


def _citation_key(d: Dict[str, Any]) -> str:
    """Dedup key: verbatim text, else alt_title, else author|title (lowercased).

    Deterministic bibliography records (source-url split) store the verbatim on the
    MENTION with top-level verbatim_reference=None and title='Unknown' — so without the
    mention/alt_title fallback EVERY such record keys to '|unknown' and 15/16 wrongly
    collapse in the resolver dedup (only one ever reaches NARA)."""
    cit = d.get("citation") or {}
    verbatim = _stripped_verbatim(d, cit)
    return (
        verbatim
        or str(cit.get("alt_title") or "").strip()
        or f"{cit.get('author', '')}|{cit.get('title', '')}".strip()
    ).lower()


def _stripped_verbatim(d: Dict[str, Any], cit: Dict[str, Any]) -> str:
    """Stripped verbatim text for the dedup key: top-level verbatim_reference / citation.verbatim,
    else the first non-empty mention verbatim_reference, else ""."""
    verbatim = str(d.get("verbatim_reference") or cit.get("verbatim") or "").strip()
    if verbatim:
        return verbatim
    for m in d.get("mentions") or []:
        mv = str(m.get("verbatim_reference") or "").strip()
        if mv:
            return mv
    return ""


def _reuse_cached_resolution(data: Dict[str, Any], cached: Dict[str, Any]) -> None:
    """Copy resolution fields from a prior identical citation onto ``data``."""
    for fld in _REUSE_FIELDS:
        if fld in cached:
            data[fld] = cached[fld]


def _queue_entry(f_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Build a human-disposition review-queue record for an unresolved citation."""
    return {
        "file": f_name,
        "citation": data.get("citation") or {},
        "verbatim_reference": data.get("verbatim_reference", ""),
        "reason": "not found online — needs human disposition",
    }


def _process_bib_file(
    f: Path,
    grok_client: Any,
    config: Optional[Dict],
    resolved_by_key: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[str, bool, str]]:
    """Resolve one bibliography file in place. Returns (status, was_deduped) or
    None if the file should be skipped/errored (caller records the stat)."""
    data = json.loads(f.read_text(encoding="utf-8"))
    # Skip only records with an actual online copy (resource_urls). A record marked
    # "resolved" by a prior RG-IDENTIFY pass but with ZERO resource_urls has no digitized
    # copy yet — re-process it so the online sources (NARA catalog / OpenSERP / archive.org /
    # Gutenberg) get a chance to find one.
    if data.get("search_status") == "resolved" and data.get("resource_urls"):
        return ("skipped", False, "")

    cached = _resolve_or_reuse(data, grok_client, config, resolved_by_key)

    from src.utils.file_lock import write_json_with_lock

    status = data.get("search_status") or "not_found"
    # Pop the transient-outage marker (never persisted) and derive the accounting status: a
    # not_found that happened while OpenSERP was down is reported as `backend_down`, not an
    # authoritative absence. On-disk search_status is unchanged (schema-valid).
    account_status = _consume_backend_down(data, status)

    # Narrative footnote, not a bibliographic reference: the deterministic pass captures every
    # non-stub footnote as a bibliography candidate, but the resolver recognizes some as
    # narrative/factual prose ("An American heavy tank did not reach the theater until 1945").
    # Those do not belong in output/bibliography/ — relocate the content (verbatim text +
    # provenance) to output/narrative_notes/ and remove the misfiled bibliography file. Return
    # the verbatim so the caller can still log it in the not_citation_samples OBSERV (the file
    # is gone after relocation, so the caller must not re-read it).
    if status == "not_citation":
        sample = (data.get("mentions") or [{}])[0].get("verbatim_reference", "") or (
            data.get("verbatim_reference") or ""
        )
        _relocate_narrative_note(f, data)
        return (status, cached is not None, sample)

    written = write_json_with_lock(f, data, entity="bibliography")
    if not written:
        # The schema guard BLOCKED the write (schema-invalid record). The resolution did NOT persist,
        # so it must NOT be reported as resolved — that is silent data loss masked as success (the
        # exact failure mode that hid the availability/source_metadata schema drift). Raise so the
        # caller's existing error channel (_resolve_one_bib_file: except -> stats["errors"] += 1)
        # counts it as an error and does NOT re-queue it (a schema block is a code defect, not a
        # human-review item).
        raise RuntimeError(
            f"schema guard refused bibliography write for {f.name} (status={status})"
        )
    # OBSERV (bibliography chain): attribute each resolution to its source so the
    # NARA vs archive_org vs local-holding breakdown (and NARA success rate) is visible.
    if status == "resolved":
        _observe_resolved(f, data)
    return (account_status, cached is not None, "")


def _consume_backend_down(data: Dict[str, Any], status: str) -> str:
    """Pop the internal `_backend_down` marker (set when OpenSERP was unavailable while resolving
    this entry) so it is NEVER persisted (the bibliography schema is additionalProperties=False),
    and remap a resulting `not_found` to the ACCOUNTING status `backend_down`. The on-disk
    `search_status` stays schema-valid (`not_found`); only the run's telemetry distinguishes a
    transient outage from an authoritative absence. Never raises."""
    try:
        backend_down = bool(data.pop("_backend_down", False))
    except Exception:  # noqa: BLE001 - defensive
        backend_down = False
    if backend_down and status == "not_found":
        return "backend_down"
    return status


def _resolve_or_reuse(
    data: Dict[str, Any],
    grok_client: Any,
    config: Optional[Dict],
    resolved_by_key: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Reuse a prior identical citation's resolution if cached, else resolve in place and cache
    it. Returns the cached record used (or None if this was a fresh resolution)."""
    key = _citation_key(data)
    cached = resolved_by_key.get(key) if key else None
    if cached is not None:
        _reuse_cached_resolution(data, cached)
    else:
        resolve_bibliography_entry(data, grok_client, config)
        if key:
            resolved_by_key[key] = data
    return cached


def _observe_resolved(f: Path, data: Dict[str, Any]) -> None:
    """OBSERV-log a resolved bibliography record (source + resource-url count). Fail-safe."""
    try:
        src = (
            str(data.get("search_source", "?"))
            .replace("\n", " ")
            .replace("\r", " ")[:40]
        )
        logger.info(
            "OBSERV bib.resolved %s: source=%s urls=%d",
            f.name,
            src,
            len(data.get("resource_urls") or []),
        )
    except Exception:  # noqa: BLE001 - observability must never break the resolver
        pass


def _relocate_narrative_note(f: Path, data: Dict[str, Any]) -> None:
    """Move a not_citation record out of output/bibliography/ into output/narrative_notes/.

    These are narrative/factual footnotes (not bibliographic references) that the deterministic
    capture pass stored as bibliography candidates. We preserve the footnote text + provenance
    (book/chapter/footnote + source_url) as a narrative note and delete the misfiled bibliography
    file. Fail-safe: a relocation error must never break resolution (the record stays put).
    """
    try:
        from src.utils.file_lock import write_json_with_lock

        mention = (data.get("mentions") or [{}])[0]
        note = {
            "NarrativeNoteID": data.get("BibliographyID") or str(ulid.new()),
            "text": mention.get("verbatim_reference")
            or (data.get("citation") or {}).get("alt_title")
            or data.get("alt_title")
            or "",
            "book": mention.get("book"),
            "chapter": mention.get("chapter"),
            "reference_type": mention.get("reference_type"),
            "reference_number": mention.get("reference_number"),
            "source_url": mention.get("source_url"),
            "mentions": data.get("mentions") or [],
            "origin": "footnote_narrative",
            "_last_updated": data.get("_last_updated"),
        }
        notes_dir = f.parent.parent / "narrative_notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        # Deterministic filename: reuse the bib file's stem so re-runs are idempotent.
        out = notes_dir / f.name
        write_json_with_lock(out, note, entity="narrative_notes")
        # VERIFY the narrative note landed before removing the bibliography source — never delete
        # the only copy on an unconfirmed write (HIGH#4).
        if not _narrative_note_written(out):
            logger.warning(
                "narrative relocate ABORTED for %s: narrative_notes write unconfirmed",
                f.name,
            )
            return
        f.unlink(missing_ok=True)
        # MOVE (not delete) the stale S3 object to a recoverable archive path, and clean sibling
        # per-writer shards so the additive full-corpus reconcile cannot resurrect the zombie.
        _archive_s3_bibliography_object(f.name)
        _cleanup_bibliography_shards(f)
        logger.info(
            "OBSERV bib.relocated_narrative %s -> narrative_notes/ (archived)", f.name
        )
    except Exception as e:  # noqa: BLE001 - relocation must never break the resolver
        logger.warning("narrative relocate failed for %s: %s", f.name, e)


def _narrative_note_written(out: Path) -> bool:
    """Confirm the relocated narrative note exists + is valid JSON (verify-before-delete)."""
    try:
        return out.is_file() and isinstance(
            json.loads(out.read_text(encoding="utf-8")), dict
        )
    except Exception:  # noqa: BLE001
        return False


def _cleanup_bibliography_shards(f: Path) -> None:
    """Remove sibling per-writer shards for THIS record's hash (``<stem>__<writer>.json``) that also
    carry the misfiled record, locally and in the archive, so the additive full-corpus reconcile
    cannot re-materialize the relocated narrative note as a bibliography zombie. Scoped to this
    record's stem so unrelated citations' shards are never collaterally removed. Best-effort, run
    post-verify; a failure is logged at WARNING because a surviving shard can silently resurrect the
    note on the next reconcile.
    """
    try:
        for shard in f.parent.glob(f"{f.stem}__*.json"):
            shard.unlink(missing_ok=True)
            _archive_s3_bibliography_object(shard.name)
    except Exception as e:  # noqa: BLE001
        logger.warning("bib shard cleanup failed near %s: %s", f.name, e)


def _archive_s3_bibliography_object(filename: str) -> None:
    """MOVE the stale ``output/bibliography/<filename>`` S3 object to ``relocated/bibliography/``
    (copy-then-delete) rather than deleting it, so a mis-relocation can be reconstructed. The
    ``relocated/`` prefix has a 60-day expiration lifecycle (see cloudformation/storage.yaml) and is
    outside ``output/`` so the additive output sync cannot pull it back as a zombie. No-op outside
    S3 mode; best-effort and fail-safe."""
    bucket = os.environ.get("S3_BUCKET") or os.environ.get("DATA_BUCKET")
    if not bucket:
        return  # local/test mode — nothing in S3 to move
    # Defense-in-depth: reduce to a basename so a future/less-trusted caller cannot inject "/" or
    # ".." into the S3 key (today callers always pass a Path.name, which is already safe).
    filename = os.path.basename(str(filename))
    if not filename:
        return
    src_key = f"output/bibliography/{filename}"
    dst_key = f"relocated/bibliography/{filename}"
    try:
        import boto3

        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        # Copy to the archive path first; only delete the source if the copy succeeds (a move that
        # never loses the object on a partial failure).
        s3.copy_object(
            Bucket=bucket, CopySource={"Bucket": bucket, "Key": src_key}, Key=dst_key
        )
        s3.delete_object(Bucket=bucket, Key=src_key)
        logger.debug("Archived stale S3 bibliography object %s -> %s", src_key, dst_key)
    except Exception as e:  # noqa: BLE001 - S3 archive is best-effort
        logger.debug("S3 bibliography archive skipped for %s: %s", filename, e)


def _account_bib_status(stats: Dict[str, int], status: str, was_deduped: bool) -> bool:
    """Update stats for one processed file. Returns True if it needs queueing
    (a real, non-deduped citation that was not found online)."""
    if was_deduped:
        stats["deduped"] += 1
    stats[status if status in stats else "not_found"] += 1
    # backend_down is a TRANSIENT outage, not an authoritative miss — a later run retries it, so
    # it must NOT be queued for human "not found online" disposition (that would launder a
    # transient outage into a permanent human-review item).
    return (
        status
        not in (
            "resolved",
            "not_citation",
            "skipped",
            "held_unprocessed",
            "backend_down",
        )
        and not was_deduped
    )


def _resolve_one_bib_file(
    f,
    grok_client: Any,
    config: Dict,
    resolved_by_key: Dict,
    stats: Dict,
    not_citation_samples: List,
    review_queue: List,
) -> None:
    """Resolve one bibliography file (fail-safe): count errors, collect not_citation samples, and
    queue a real-but-unresolved citation for human review. Mutates stats/samples/queue in place.
    """
    try:
        result = _process_bib_file(f, grok_client, config, resolved_by_key)
    except (json.JSONDecodeError, OSError):
        stats["errors"] += 1
        return
    except Exception as e:  # resolver failure — count, keep going
        logger.warning("Failed to resolve %s: %s", f.name, e)
        stats["errors"] += 1
        return

    status, was_deduped, nc_sample = result
    if status == "not_citation" and nc_sample and len(not_citation_samples) < 5:
        not_citation_samples.append(_CTRL.sub(" ", nc_sample)[:80])
    if _account_bib_status(stats, status, was_deduped):
        # Real citation, unresolved online → human disposition.
        data = json.loads(f.read_text(encoding="utf-8"))
        review_queue.append(_queue_entry(f.name, data))
        stats["queued"] += 1


def resolve_bibliography_dir(
    bib_dir: Path,
    grok_client: Any = None,
    config: Optional[Dict] = None,
    max_items: Optional[int] = None,
) -> Dict[str, int]:
    """Resolve all unresolved bibliography entries in a directory.

    Grabs what is legitimately available online (Archive.org/Gutenberg/HathiTrust/
    LOC/NARA-catalog, Grok-verified) or produces an actionable archive locator;
    every real citation we cannot grab is queued for human disposition
    (``review_queue.json``). Narrative prose (``not_citation``) is skipped.

    Returns stats: {resolved, not_found, skipped, errors, not_citation, queued,
    deduped}.
    """
    stats = {
        k: 0
        for k in (
            "resolved",
            "not_found",
            "skipped",
            "errors",
            "not_citation",
            "queued",
            "deduped",
            "held_unprocessed",
            "reference_identified",
            "backend_down",
        )
    }
    skip_files = {"index.json", "review_queue.json"}
    resolved_by_key: Dict[str, Dict[str, Any]] = {}
    review_queue: List[Dict[str, Any]] = []
    not_citation_samples: List[str] = []
    processed = 0

    # Build the local-holdings index ONCE and inject it so each entry can be
    # short-circuited to a source we already hold (MS # B-405 -> B405.pdf).
    config = dict(config or {})
    if "holdings_index" not in config:
        config["holdings_index"] = build_holdings_index()
    logger.info("Local holdings index: %d source docs", len(config["holdings_index"]))

    for f in sorted(bib_dir.glob("*.json")):
        if f.name in skip_files:
            continue
        if max_items and processed >= max_items:
            break
        processed += 1
        _resolve_one_bib_file(
            f,
            grok_client,
            config,
            resolved_by_key,
            stats,
            not_citation_samples,
            review_queue,
        )

    _write_review_queue(bib_dir, review_queue)
    _log_bib_resolution_summary(stats, processed, not_citation_samples)
    return stats


def _write_review_queue(bib_dir: Path, review_queue: List[Dict[str, Any]]) -> None:
    """Write the human-disposition review queue, if any entries were queued. Fail-safe."""
    if not review_queue:
        return
    try:
        (bib_dir / "review_queue.json").write_text(
            json.dumps(review_queue, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("Failed to write bibliography review_queue.json: %s", e)


def _log_bib_resolution_summary(
    stats: Dict[str, int], processed: int, not_citation_samples: List[str]
) -> None:
    """Emit the human-readable resolution summary + the OBSERV bib.resolve line (fail-safe)."""
    logger.info(
        "Bibliography resolution: %d resolved, %d not found (%d queued for review), "
        "%d not-citation, %d deduped, %d skipped, %d errors",
        stats["resolved"],
        stats["not_found"],
        stats["queued"],
        stats["not_citation"],
        stats["deduped"],
        stats["skipped"],
        stats["errors"],
    )
    try:
        logger.info(
            "OBSERV bib.resolve in=%d resolved=%d ref_identified=%d not_found=%d not_citation=%d "
            "deduped=%d queued=%d held=%d skipped=%d errors=%d not_citation_samples=%s",
            processed,
            stats["resolved"],
            stats["reference_identified"],
            stats["not_found"],
            stats["not_citation"],
            stats["deduped"],
            stats["queued"],
            stats["held_unprocessed"],
            stats["skipped"],
            stats["errors"],
            not_citation_samples,
        )
    except Exception:  # noqa: BLE001 - observability must never break resolution
        pass
    # Canonical alias + anomaly (the pre-existing bib.resolve line is kept for back-compat but is a
    # non-standard name and had no anomaly; a high not-found/error rate is the signal to surface).
    try:
        logger.info(
            "OBSERV bibliography.resolve_summary in=%d resolved=%d ref_identified=%d "
            "not_found=%d backend_down=%d not_citation=%d queued=%d held=%d errors=%d",
            processed,
            stats["resolved"],
            stats["reference_identified"],
            stats["not_found"],
            stats.get("backend_down", 0),
            stats["not_citation"],
            stats["queued"],
            stats["held_unprocessed"],
            stats["errors"],
        )
        # backend_down is a TRANSIENT outage recorded distinctly from not_found so it is never
        # read as "no copy exists". Surface it independently (a later run retries these).
        if stats.get("backend_down", 0) > 0:
            logger.warning(
                "OBSERV bibliography.resolve_anomaly backend_down=%d "
                "(OpenSERP outage — these are NOT authoritative not_found, retry next run)",
                stats["backend_down"],
            )
        # A real citation is one we could plausibly resolve: exclude not_citation (prose) from the
        # denominator. 0 of those resolved/identified, or any errors, is the anomaly.
        resolvable = processed - stats["not_citation"]
        got = (
            stats["resolved"]
            + stats["reference_identified"]
            + stats["held_unprocessed"]
        )
        if resolvable > 0 and got == 0:
            logger.warning(
                "OBSERV bibliography.resolve_anomaly 0-of-%d resolvable citations resolved "
                "(resolver services down / all queued-for-review?)",
                resolvable,
            )
        elif stats["errors"] > 0:
            logger.warning(
                "OBSERV bibliography.resolve_anomaly errors=%d of in=%d",
                stats["errors"],
                processed,
            )
    except Exception:  # noqa: BLE001 - observability must never break resolution
        pass
