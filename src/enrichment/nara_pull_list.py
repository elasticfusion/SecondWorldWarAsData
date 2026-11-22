"""The NARA pull list: cited records to retrieve in person at NARA (Archives II, College Park), and
the scans brought back (owner 2026-11-21).

Two kinds of item, built on demand from the bibliography records and the review queue's NARA finds
(nothing to keep in step):

* ``exact`` - a NARA catalog record a human approved as the cited document, known to hold no
  scans (``source_metadata.findings[]`` with ``digital_objects == 0``). One item per NARA record
  (``nara-<naId>``), listing every citation it serves.
* ``locate_first`` - a citation whose record group is known (``archive_reference_number``) but
  that matched no catalog record (e.g. combat-interview files described only at series level).
  One item per citation (``rg<N>-<BibliographyID>``): the archivist or a finding aid locates it.

A person tracks each item: needed -> requested -> pulled -> scanned -> ingested, or unobtainable,
with notes, who and when (``pull#<item_id>`` rows in the cache table; operator-written only, the
pipeline roles are denied). A scan brought back is added with ``add_scan``: it is uploaded as a new
source under ``contentrepository/nara-scans/<slug>.<ext>`` (the slug, set here before upload, is
immutable) and bound to its BibliographyID by a ``pullscan#<slug>`` row carrying its provenance
(NARA identifiers, who scanned it and when, the file's sha256, the licence basis). Phase 3 binds the
bibliography record to it (``scan_bindings`` / ``apply_scan``). A scan a human added needs no URL
review (trusted = human-added, owner 2026-11-21).
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.ingestion.book_slug import book_slug, is_safe_slug
from src.schemas import ULID_REGEX
from src.utils.url_hygiene import scrub

logger = logging.getLogger(__name__)

STATUSES = ("needed", "requested", "pulled", "scanned", "ingested", "unobtainable")
EXACT, LOCATE_FIRST = "exact", "locate_first"
PULL_PREFIX, SCAN_PREFIX = "pull#", "pullscan#"
SCAN_DIR = "contentrepository/nara-scans"
SCAN_SUFFIXES = (".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png")
MAX_SCAN_BYTES = 2 * 1024**3  # a box scanned to one PDF; larger: split with --part
_HISTORY = 50  # status changes kept per item
_RG = re.compile(r"\bRG\s*(\d{1,3})\b", re.IGNORECASE)
_NARA_SOURCE = "nara_catalog"


@dataclass
class PullItem:
    item_id: str
    kind: str
    record_group: Optional[int]
    naId: Optional[str] = None
    title: str = ""
    level: str = ""
    series_path: List[str] = field(default_factory=list)
    holding: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "needed"
    notes: str = ""
    updated_by: str = ""
    updated_at: int = 0

    @property
    def cited_by(self) -> int:
        return len(self.citations)


def record_group(rec: Dict[str, Any]) -> Optional[int]:
    """The NARA record group a citation names (``RG 407`` -> 407), or None."""
    m = _RG.search(str(rec.get("archive_reference_number") or ""))
    return int(m.group(1)) if m else None


def _verbatim(rec: Dict[str, Any]) -> str:
    from src.extraction.bibliography import _citation_text

    return scrub(_citation_text(rec))[:300]


def _citation(rec: Dict[str, Any]) -> Dict[str, Any]:
    books = sorted(
        {
            str(m.get("book"))
            for m in rec.get("mentions") or []
            if isinstance(m, dict) and m.get("book")
        }
    )
    return {
        "BibliographyID": str(rec.get("BibliographyID") or ""),
        "verbatim": _verbatim(rec),
        "books": books,
        "mentions": len(rec.get("mentions") or []),
    }


def _approved_paper_records(rec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The approved NARA findings of a record known to hold no scans."""
    found = (rec.get("source_metadata") or {}).get("findings") or []
    return [
        f
        for f in found
        if isinstance(f, dict)
        and f.get("source") == _NARA_SOURCE
        and f.get("verified_by") == "grok+human"
        and f.get("digital_objects") == 0
        and str(f.get("naId") or "").isdigit()
    ]


def _event_details(events: Dict[str, Dict[str, Any]], url: str) -> Dict[str, Any]:
    ev = events.get(url) or {}
    d = (ev.get("request_params") or {}).get("download")
    return d if isinstance(d, dict) else {}


def _locatable(rec: Dict[str, Any]) -> bool:
    """A citation to locate at NARA: its record group (a NARA arrangement) is known and no copy
    or catalog record was approved or added for it."""
    return bool(
        record_group(rec) is not None
        and not rec.get("resource_urls")
        and not rec.get("download_path")
        and rec.get("search_status")
        not in ("not_citation", "resolved", "held_unprocessed")
    )


def build_items(
    records: Iterable[Dict[str, Any]],
    nara_events: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[PullItem]:
    """The pull list from bibliography records (and the queued NARA finds, for each record's
    catalog title, level and series path). Sorted for a visit: exact items first, then by record
    group, series path, title, most-cited first."""
    events = {str(e.get("endpoint") or ""): e for e in nara_events or []}
    items: Dict[str, PullItem] = {}
    for rec in records:
        if isinstance(rec, dict) and rec.get("BibliographyID"):
            _add_record(items, rec, events)
    return sorted(items.values(), key=_visit_order)


def _add_record(
    items: Dict[str, PullItem], rec: Dict[str, Any], events: Dict[str, Any]
) -> None:
    if bound_to_scan(rec):
        return  # its scan was brought back and added
    paper = _approved_paper_records(rec)
    for f in paper:
        _add_exact(items, rec, f, _event_details(events, str(f.get("url") or "")))
    if not paper and _locatable(rec):
        _add_locate_first(items, rec)


def _add_exact(items, rec, finding, details) -> None:
    naid = str(finding["naId"])
    item = items.get(f"nara-{naid}")
    if item is None:
        item = items[f"nara-{naid}"] = PullItem(
            item_id=f"nara-{naid}",
            kind=EXACT,
            record_group=record_group(rec),
            naId=naid,
            title=scrub(details.get("nara_title") or "")[:200],
            level=scrub(details.get("level") or "")[:40],
            series_path=[scrub(s)[:200] for s in details.get("series_path") or []][:8],
            holding=scrub(rec.get("archive_physical_address") or "")[:200],
        )
    if item.record_group is None:
        item.record_group = record_group(rec)  # the first citation that names one
    if not item.holding:
        item.holding = scrub(rec.get("archive_physical_address") or "")[:200]
    item.citations.append(_citation(rec))


def _add_locate_first(items, rec) -> None:
    rg = record_group(rec)
    bid = str(rec["BibliographyID"])
    items[f"rg{rg}-{bid}"] = PullItem(
        item_id=f"rg{rg}-{bid}",
        kind=LOCATE_FIRST,
        record_group=rg,
        title=_verbatim(rec)[:200],
        holding=scrub(rec.get("archive_physical_address") or "")[:200],
        citations=[_citation(rec)],
    )


def _visit_order(item: PullItem):
    return (
        item.kind != EXACT,
        item.record_group if item.record_group is not None else 9999,
        [s.lower() for s in item.series_path],
        item.title.lower(),
        -item.cited_by,
        item.item_id,
    )


# --- statuses (operator rows in the cache table) -------------------------------------------------


def _row(table: Any, key: str) -> Optional[Dict[str, Any]]:
    item = table.get_item(Key={"cache_key": key}, ConsistentRead=True).get("Item")
    if not item:
        return None
    try:
        out = json.loads(item.get("response") or "{}")
        return out if isinstance(out, dict) else None
    except ValueError:
        return None


def apply_statuses(items: List[PullItem], table: Any) -> List[PullItem]:
    """Fill each item's status, notes, who and when from its ``pull#`` row (needed when none)."""
    for item in items:
        row = _row(table, PULL_PREFIX + item.item_id) or {}
        if row.get("status") in STATUSES:
            item.status = row["status"]
            item.notes = scrub(row.get("notes") or "")[:500]
            item.updated_by = scrub(row.get("by") or "")[:60]
            item.updated_at = int(row.get("at") or 0)
    return items


def _valid_item_id(item_id: str) -> bool:
    return bool(
        re.fullmatch(r"(nara-\d{1,15}|rg\d{1,3}-[0-9A-HJKMNP-TV-Z]{26})", item_id)
    )


def set_status(
    table: Any, item_id: str, status: str, by: str, notes: str = ""
) -> Dict[str, Any]:
    """Record a person's status change for one item (history kept). Raises ValueError on a bad
    item id, status or name."""
    if not _valid_item_id(item_id):
        raise ValueError(f"not a pull-list item id: {item_id!r}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    who = scrub(by).strip()[:60]
    if not who:
        raise ValueError("--by (who made the change) is required")
    row = _row(table, PULL_PREFIX + item_id) or {}
    now = int(time.time())
    history = list(row.get("history") or [])[-(_HISTORY - 1) :]
    history.append(
        {"status": status, "by": who, "at": now, "notes": scrub(notes)[:500]}
    )
    new = {
        "status": status,
        "by": who,
        "at": now,
        "notes": scrub(notes)[:500],
        "history": history,
    }
    table.put_item(
        Item={"cache_key": PULL_PREFIX + item_id, "response": json.dumps(new)}
    )
    logger.info("OBSERV nara_pull.status item=%s status=%s by=%r", item_id, status, who)
    return new


# --- export ------------------------------------------------------------------------------------

_CSV_FIELDS = (
    "item_id",
    "kind",
    "status",
    "record_group",
    "series_path",
    "level",
    "title",
    "naId",
    "catalog_url",
    "cited_by",
    "citations",
    "BibliographyIDs",
    "holding",
    "notes",
    "updated_by",
)


def _csv_safe(value: Any) -> str:
    """A cell a spreadsheet will not run as a formula (``=``, ``+``, ``-``, ``@`` lead)."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv_row(it: PullItem) -> Dict[str, str]:
    raw = {
        "item_id": it.item_id,
        "kind": it.kind,
        "status": it.status,
        "record_group": it.record_group or "",
        "series_path": " > ".join(it.series_path),
        "level": it.level,
        "title": it.title,
        "naId": it.naId or "",
        "catalog_url": f"https://catalog.archives.gov/id/{it.naId}" if it.naId else "",
        "cited_by": it.cited_by,
        "citations": " || ".join(c["verbatim"] for c in it.citations),
        "BibliographyIDs": " ".join(c["BibliographyID"] for c in it.citations),
        "holding": it.holding,
        "notes": it.notes,
        "updated_by": it.updated_by,
    }
    return {k: _csv_safe(v) for k, v in raw.items()}


def export_csv(items: List[PullItem], path: Path) -> None:
    fields: List[str] = list(_CSV_FIELDS)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for it in items:
            w.writerow(_csv_row(it))


def _md(text: Any) -> str:
    """Text for a Markdown table cell: no line breaks, and no markup a renderer would act on."""
    out = scrub(text).replace("\\", "\\\\")
    for ch in "|`*_[]()<>#!":
        out = out.replace(ch, "\\" + ch)
    return out


def export_markdown(items: List[PullItem], path: Path) -> None:
    """A printable list for the visit, grouped by record group."""
    lines = ["# NARA pull list", ""]
    for kind, heading in (
        (EXACT, "Records identified in the catalog"),
        (
            LOCATE_FIRST,
            "Locate first (record group known; ask the archivist or use a finding aid)",
        ),
    ):
        group = [i for i in items if i.kind == kind]
        lines += [f"## {heading} ({len(group)})", ""]
        rg_seen: Optional[int] = -1
        for it in group:
            if it.record_group != rg_seen:
                rg_seen = it.record_group
                lines += ["", f"### RG {rg_seen if rg_seen is not None else '?'}", ""]
                lines += [
                    "| [ ] | Item | Series / level | Title | Cited by | Status |",
                    "|---|---|---|---|---|---|",
                ]
            lines.append(
                f"| [ ] | {_md(it.item_id)} | {_md(' > '.join(it.series_path) or it.level)} | "
                f"{_md(it.title)} | {it.cited_by} | {_md(it.status)} |"
            )
        lines.append("")
    # a printable report for people, not an entity record (raw-write baseline 70, 2026-11-21)
    path.write_text("\n".join(lines), encoding="utf-8")


# --- scans brought back ------------------------------------------------------------------------


def scan_slug(
    item: Optional[PullItem], bibliography_id: str, part: Optional[int]
) -> str:
    """The new source's slug, set before upload (immutable): ``NARA-RG<N>-<naId>`` for a catalog
    record, else ``NARA-RG<N>-<id tail>``; ``-p<N>`` for one of several files."""
    rg = f"RG{item.record_group}" if item and item.record_group is not None else "RG"
    tail = item.naId if item and item.naId else bibliography_id[-10:]
    slug = f"NARA-{rg}-{tail}" + (f"-p{part}" if part else "")
    if not is_safe_slug(slug):
        raise ValueError(f"unsafe slug {slug!r}")
    return slug


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_scan_file(path: Path) -> Dict[str, Any]:
    """The scan's size and sha256. Raises ValueError for a missing, oversized or unsupported file."""
    if not path.is_file():
        raise ValueError(f"no such file: {path}")
    suffix = path.suffix.lower()
    if suffix not in SCAN_SUFFIXES:
        raise ValueError(
            f"a scan must be one of {', '.join(SCAN_SUFFIXES)} (got {suffix!r})"
        )
    size = path.stat().st_size
    if not 0 < size <= MAX_SCAN_BYTES:
        raise ValueError(
            f"scan size {size} bytes is outside 1..{MAX_SCAN_BYTES}; split it with --part"
        )
    return {"suffix": suffix, "size": size, "sha256": _sha256(path)}


def _checked_person(bibliography_id: str, scanned_on: str, by: str) -> str:
    """The validated name of who scanned it. Raises ValueError on a bad id, date or name."""
    if not ULID_REGEX.fullmatch(bibliography_id or ""):
        raise ValueError("--bib must be a BibliographyID (ULID)")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", scanned_on or ""):
        raise ValueError("--scanned-on must be YYYY-MM-DD")
    who = scrub(by).strip()[:60]
    if not who:
        raise ValueError("--by (who scanned it) is required")
    return who


def _licence(us_government_record: bool) -> Dict[str, Optional[str]]:
    """A person states it: a U.S. Government record is public domain (17 U.S.C. 105); captured
    foreign records (e.g. RG 242) and donated papers are not, so it is never assumed."""
    if not us_government_record:
        return {"license": None, "license_basis": None}
    return {
        "license": "public_domain",
        "license_basis": "U.S. Government record (17 U.S.C. 105), stated by the scanner",
    }


def binding(
    *,
    slug: str,
    key: str,
    bibliography_id: str,
    item: Optional[PullItem],
    file_info: Dict[str, Any],
    by: str,
    scanned_on: str,
    us_government_record: bool,
    notes: str = "",
) -> Dict[str, Any]:
    """The provenance row of an added scan (``pullscan#<slug>``). Raises ValueError on bad input."""
    who = _checked_person(bibliography_id, scanned_on, by)
    if book_slug(key) != slug:
        raise ValueError(f"key {key!r} would not be named {slug!r}")
    return {
        "slug": slug,
        "key": key,
        "BibliographyID": bibliography_id,
        "item_id": item.item_id if item else None,
        "naId": item.naId if item else None,
        "record_group": item.record_group if item else None,
        "sha256": file_info["sha256"],
        "size": file_info["size"],
        "scanned_on": scanned_on,
        "by": who,
        "added_at": int(time.time()),
        "acquisition": "nara_research_visit",
        **_licence(us_government_record),
        "notes": scrub(notes)[:500],
    }


def _scan_rows(table: Any) -> Iterable[Dict[str, Any]]:
    """Every ``pullscan#`` row's parsed body (paginated scan of the cache table)."""
    from boto3.dynamodb.conditions import Attr

    kwargs: Dict[str, Any] = {
        "FilterExpression": Attr("cache_key").begins_with(SCAN_PREFIX)
    }
    while True:
        resp = table.scan(**kwargs)
        for it in resp.get("Items", []):
            row = _read_row(it.get("response"))
            if row is not None:
                yield row
        if not resp.get("LastEvaluatedKey"):
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _read_row(text: Any) -> Optional[Dict[str, Any]]:
    try:
        row = json.loads(text or "{}")
    except (TypeError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def _valid_key(row: Dict[str, Any]) -> bool:
    """The row's key is exactly ``nara-scans/<its slug><an allowed suffix>``."""
    slug = str(row.get("slug") or "")
    key = str(row.get("key") or "")
    suffix = key[len(f"{SCAN_DIR}/{slug}") :]
    return bool(
        is_safe_slug(slug)
        and slug.startswith("NARA-")
        and key == f"{SCAN_DIR}/{slug}{suffix}"
        and suffix in SCAN_SUFFIXES
        and book_slug(key) == slug
    )


def _valid_fields(row: Dict[str, Any]) -> bool:
    """Every other field Phase 3 copies has the shape the CLI writes."""
    return bool(
        ULID_REGEX.fullmatch(str(row.get("BibliographyID") or ""))
        and row.get("license") in (None, "public_domain")
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("scanned_on") or ""))
        and re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256") or ""))
        and (row.get("naId") is None or str(row.get("naId")).isdigit())
    )


def _valid_scan(row: Dict[str, Any]) -> bool:
    """A provenance row Phase 3 may act on (data read back from the store, never trusted as-is)."""
    return _valid_key(row) and _valid_fields(row)


def scan_bindings(table: Any) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Every added scan by BibliographyID (all parts, by slug), or None when the rows could not be
    read. A row that fails the checks is said, never used."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for row in _scan_rows(table):
            if _valid_scan(row):
                out.setdefault(str(row["BibliographyID"]), []).append(row)
            else:
                logger.error(
                    "OBSERV nara_pull.scan_row_invalid slug=%r status=error - a pullscan# row "
                    "fails its checks and is ignored; inspect it (only scripts/nara_pull_list.py "
                    "writes these)",
                    scrub(row.get("slug") or "")[:60],
                )
        for rows in out.values():
            rows.sort(key=lambda r: str(r["slug"]))
        return out
    except Exception as e:  # noqa: BLE001
        logger.error(
            "OBSERV nara_pull.scans_unreadable err=%s status=error - added NARA scans are not bound "
            "to their bibliography records this run; check read access to the cache table",
            type(e).__name__,
        )
        return None


def _scans_for(
    rec: Dict[str, Any], scans: Optional[Dict[str, List[Dict[str, Any]]]]
) -> List[Dict[str, Any]]:
    """The scans added for this record, or for an id it absorbed (every part)."""
    if not scans:
        return []
    ids = [str(rec.get("BibliographyID") or "")] + [
        str(i) for i in rec.get("merged_from") or []
    ]
    return [row for i in ids for row in scans.get(i, [])]


def bound_to_scan(rec: Dict[str, Any]) -> bool:
    """The record's held copy is a scan a person added (and nothing better came since)."""
    return str(rec.get("download_path") or "").startswith(SCAN_DIR + "/") and (
        rec.get("search_status") != "resolved"
    )


def _add_scan_finding(rec: Dict[str, Any], row: Dict[str, Any]) -> None:
    meta = rec.get("source_metadata")
    if not isinstance(meta, dict):
        meta = rec["source_metadata"] = {}
    findings = meta.setdefault("findings", [])
    if not isinstance(findings, list):
        return
    if any(isinstance(f, dict) and f.get("url") == row["key"] for f in findings):
        return
    findings.append(
        {
            "url": row["key"],
            "source": "nara_research_visit",
            "verified_by": "human",
            "naId": row.get("naId"),
            "scanned_on": row.get("scanned_on"),
            "scanned_by": scrub(row.get("by") or "")[:60],
            "sha256": row.get("sha256"),
            "license_basis": row.get("license_basis") if row.get("license") else None,
        }
    )


def apply_scan(
    rec: Dict[str, Any], scans: Optional[Dict[str, List[Dict[str, Any]]]]
) -> bool:
    """Bind a bibliography record to the scans a person added for it (or for an id it absorbed):
    every part becomes a ``verified_by: human`` finding; the first becomes ``download_path`` and
    the record is held (``held_unprocessed`` until the pipeline has processed it). A record that
    already holds another copy keeps it (the scan is only a finding, said). Returns True when the
    record changed."""
    rows = _scans_for(rec, scans)
    if not rows:
        return False
    before = json.dumps(rec, sort_keys=True, default=str)
    for row in rows:
        _add_scan_finding(rec, row)
    current = str(rec.get("download_path") or "")
    if current and not current.startswith(SCAN_DIR + "/"):
        logger.warning(
            "OBSERV nara_pull.scan_not_primary bib=%s slug=%s status=warn - the record already "
            "holds %r; the scan is kept as a finding only (review which copy should be primary)",
            rec.get("BibliographyID"),
            rows[0]["slug"],
            current[:120],
        )
    else:
        rec["download_path"] = rows[0]["key"]
        _hold(rec, rows[0])
    changed = json.dumps(rec, sort_keys=True, default=str) != before
    if changed:
        logger.info(
            "OBSERV nara_pull.scan_bound bib=%s slugs=%s",
            rec.get("BibliographyID"),
            ",".join(r["slug"] for r in rows)[:200],
        )
    return changed


def _hold(rec: Dict[str, Any], row: Dict[str, Any]) -> None:
    """The record holds the scan (an approved online copy, ``resolved``, stays as it is)."""
    if rec.get("search_status") != "resolved":
        rec.update(
            search_status="held_unprocessed",
            search_source="nara_research_visit",
            availability="local",
        )
    if row.get("license") and rec.get("license") in (None, "", "unknown"):
        rec["license"] = row["license"]
