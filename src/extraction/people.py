"""People extraction from event data."""

import json
import logging
import re
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import ulid
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.grok_client import GrokClient, BatchModeCollecting
from src.extraction.starvation_signal import (
    note_empty_fulltext_skip,
    warn_all_subevents_empty,
)
from src.extraction.recall_gap import record_people_recall_gaps, _text_from_sub_event
from src.utils.json_validator import _fix_invalid_ulids
from src.utils.text_utils import normalize_name
from src.utils.yield_observer import count_nonempty_fulltext, observe_yield

from src.utils.prompt_loader import get_system_prompt

logger = logging.getLogger(__name__)

# Schema version this module targets — derived from the central per-entity map.
from src.schemas import entity_version as _entity_version

SCHEMA_TARGET = _entity_version("people")

# Compiled regex patterns for performance
_SPECIAL_CHARS_PATTERN = re.compile(r"[^\w\s-]")
_WHITESPACE_PATTERN = re.compile(r"\s+")


# Rank abbreviation normalization
RANK_ABBREVIATIONS = {
    # US/Commonwealth shared
    "Gen.": "General",
    "Gen": "General",
    "Lt. Gen.": "Lieutenant General",
    "Lt Gen": "Lieutenant General",
    "Lt.-Gen.": "Lieutenant General",
    "Maj. Gen.": "Major General",
    "Maj Gen": "Major General",
    "Maj.-Gen.": "Major General",
    "Brig. Gen.": "Brigadier General",
    "Brig Gen": "Brigadier General",
    "Col.": "Colonel",
    "Col": "Colonel",
    "Lt. Col.": "Lieutenant Colonel",
    "Lt Col": "Lieutenant Colonel",
    "Lt.-Col.": "Lieutenant Colonel",
    "Maj.": "Major",
    "Maj": "Major",
    "Capt.": "Captain",
    "Capt": "Captain",
    "Lt.": "Lieutenant",
    "Lt": "Lieutenant",
    "1st Lt.": "First Lieutenant",
    "1st Lt": "First Lieutenant",
    "2nd Lt.": "Second Lieutenant",
    "2nd Lt": "Second Lieutenant",
    "Sgt.": "Sergeant",
    "Sgt": "Sergeant",
    "Cpl.": "Corporal",
    "Cpl": "Corporal",
    "Pvt.": "Private",
    "Pvt": "Private",
    "Adm.": "Admiral",
    "Adm": "Admiral",
    "Vice Adm.": "Vice Admiral",
    "Vice Adm": "Vice Admiral",
    "Rear Adm.": "Rear Admiral",
    "Rear Adm": "Rear Admiral",
    "Cmdr.": "Commander",
    "Cmdr": "Commander",
    "Lt. Cmdr.": "Lieutenant Commander",
    "Lt Cmdr": "Lieutenant Commander",
    # British/Canadian
    "Brig.": "Brigadier",
    "Brig": "Brigadier",
    "Fld. Mshl.": "Field Marshal",
    "FM": "Field Marshal",
    "F.M.": "Field Marshal",
    "Gp. Capt.": "Group Captain",
    "Gp Capt": "Group Captain",
    "Wg. Cdr.": "Wing Commander",
    "Sqn. Ldr.": "Squadron Leader",
    "Flt. Lt.": "Flight Lieutenant",
    # French
    "Gén.": "Général",
    "Mal.": "Maréchal",
    "Cdt.": "Commandant",
    "Cdt": "Commandant",
    "Lcl.": "Lieutenant-Colonel",
    "Cne.": "Capitaine",
    # German
    "Generalfeldmarschall": "Generalfeldmarschall",
    "GFM": "Generalfeldmarschall",
    "Gen.d.Inf.": "General der Infanterie",
    "Gen.d.Art.": "General der Artillerie",
    "Gen.d.Kav.": "General der Kavallerie",
    "Gen.d.Pz.Tr.": "General der Panzertruppen",
    "Generaloberst": "Generaloberst",
    "Generalleutnant": "Generalleutnant",
    "Generalmajor": "Generalmajor",
    "Oberst": "Oberst",
    "Oberstleutnant": "Oberstleutnant",
    "Oberstlt.": "Oberstleutnant",
    "Hptm.": "Hauptmann",
    "Oblt.": "Oberleutnant",
    "SS-Obergruppenführer": "SS-Obergruppenführer",
    "SS-Gruppenführer": "SS-Gruppenführer",
    "SS-Brigadeführer": "SS-Brigadeführer",
    "SS-Oberführer": "SS-Oberführer",
}


@lru_cache(maxsize=1000)
def _normalize_rank(rank: str) -> str:
    """Normalize rank abbreviations to full names."""
    return RANK_ABBREVIATIONS.get(rank, rank)


@lru_cache(maxsize=500)
def _normalize_branch(branch: str) -> str:
    """Normalize branch names."""
    branch_map = {
        "US Army": "U.S. Army",
        "U.S Army": "U.S. Army",
        "US Navy": "U.S. Navy",
        "U.S Navy": "U.S. Navy",
        "US Air Force": "U.S. Air Force",
        "U.S Air Force": "U.S. Air Force",
        "US Marine Corps": "U.S. Marine Corps",
        "U.S Marine Corps": "U.S. Marine Corps",
    }
    return branch_map.get(branch, branch)


@lru_cache(maxsize=1000)
def _normalize_unit(unit: str) -> str:
    """Normalize unit names."""
    unit_map = {
        "OPD, War Department": "Operations Division (OPD), War Department",
        "OPD": "Operations Division (OPD), War Department",
        "Operations Division": "Operations Division (OPD), War Department",
    }
    return unit_map.get(unit, unit)


def _normalize_units(units: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Normalize unit names and carry the structured affiliation fields through."""
    normalized = []
    for u in units:
        norm_unit = _normalize_unit(u.get("unit", ""))
        normalized.append(
            {
                "unit": norm_unit,
                "from": u.get("from"),
                "to": u.get("to"),
                "GroupID": u.get("GroupID"),
                "designation": u.get("designation"),
                "echelon": u.get("echelon"),
                "unit_number": u.get("unit_number"),
            }
        )
    return normalized


def _pick_best_unit(entries: list[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick the richest unit entry from a group of duplicates: prefer the one with both
    dates, then preserve any structured affiliation field present on ANY duplicate, then drop
    None keys so records stay clean (migration-safe)."""

    # Sort by priority: both dates > one date > no dates
    def priority(x):
        has_from = x["from"] is not None
        has_to = x["to"] is not None
        return (
            has_from and has_to,
            has_from or has_to,
            x["from"] or "",
            x["to"] or "",
        )

    best = max(entries, key=priority)
    # Preserve the richest structured affiliation across duplicates: a GroupID
    # or designation present on ANY duplicate should survive onto the kept one.
    for field in ("GroupID", "designation", "echelon", "unit_number"):
        if not best.get(field):
            for e in entries:
                if e.get(field):
                    best[field] = e[field]
                    break
    # Drop keys that are None so records stay clean (migration-safe).
    return {k: v for k, v in best.items() if v is not None}


def _deduplicate_units(units: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Deduplicate and normalize units.

    Priority: Unit with dates > Unit with partial dates > Unit no dates
    """
    if not units:
        return []

    # Normalize all units (carry the structured affiliation fields through)
    normalized = _normalize_units(units)

    # Group by unit name
    groups: Dict[str, list[Dict[str, Any]]] = {}
    for u in normalized:
        key = u["unit"]
        if key not in groups:
            groups[key] = []
        groups[key].append(u)

    # For each group, keep best entry
    result = []
    for entries in groups.values():
        result.append(_pick_best_unit(entries))

    return result


def _deduplicate_awards(awards: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Deduplicate military awards.

    Priority: Award with full date > Award with partial date > Award no date
    """
    if not awards:
        return []

    # Group by (award, class)
    groups: Dict[tuple[str, Optional[str]], list[Dict[str, Any]]] = {}
    for a in awards:
        key = (a.get("award", ""), a.get("class"))
        if key not in groups:
            groups[key] = []
        groups[key].append(a)

    # For each group, keep best entry (most specific date)
    result = []
    for entries in groups.values():
        # Sort by date specificity: full date (YYYY-MM-DD) > year only > no date
        def date_priority(x):
            date = x.get("date_awarded")
            if not date:
                return (0, "")
            # Full date has dashes, year only doesn't
            has_full_date = "-" in date
            return (2 if has_full_date else 1, date)

        best = max(entries, key=date_priority)
        result.append(best)

    return result


def _deduplicate_ranks(ranks: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Deduplicate and normalize ranks.

    Priority: Rank with date > Abbreviated with date > Full no date > Abbreviated no date
    """
    if not ranks:
        return []

    # Normalize all ranks
    normalized = []
    for r in ranks:
        norm_rank = _normalize_rank(r.get("rank", ""))
        norm_branch = (
            _normalize_branch(r.get("branch", "")) if r.get("branch") else None
        )
        normalized.append(
            {
                "rank": norm_rank,
                "date": r.get("date"),
                "branch": norm_branch,
            }
        )

    # Group by (rank, branch)
    groups: Dict[tuple[str, Optional[str]], list[Dict[str, Any]]] = {}
    for r in normalized:
        key = (r["rank"], r["branch"])
        if key not in groups:
            groups[key] = []
        groups[key].append(r)

    # For each group, keep best entry
    result = []
    for entries in groups.values():
        # Sort by priority: has date (True first), then by date if both have dates
        best = max(entries, key=lambda x: (x["date"] is not None, x["date"] or ""))
        result.append(best)

    return result


class MilitaryAward(BaseModel):
    """Military award or decoration received by a person."""

    award: str
    class_: Optional[str] = Field(default=None, alias="class")
    date_awarded: Optional[str] = None

    # --- Source record / provenance (migration-safe: all optional) ---
    citation_text: Optional[str] = None  # citation (English; translated if needed)
    citation_text_original: Optional[str] = None  # verbatim source text if non-English
    citation_language: Optional[str] = (
        None  # source language (e.g. "German"); provenance
    )
    source_name: Optional[str] = None  # e.g. "American War Library", "Hall of Valor"
    source_url: Optional[str] = None  # per-recipient page the citation came from
    retrieved_date: Optional[str] = None  # ISO-8601 retrieval date
    verified: Optional[bool] = None  # True only when source recipient matched person
    # Per-source attempt log (source/outcome/error-with-URL/attempted_date) so a later
    # run knows what was tried + why it failed (no blind re-hammering). Optional.
    sourcing_attempts: Optional[list] = None

    model_config = ConfigDict(populate_by_name=True)


class MilitaryRank(BaseModel):
    """Military rank held by a person."""

    rank: str
    date: Optional[str] = None
    branch: Optional[str] = None


class UnitServed(BaseModel):
    """Military unit in which a person served.

    ``unit`` is the free-text unit name as written. The structured fields enable
    unit-scoped work (e.g. targeting a unit's own recipients page) without first
    doing full entity resolution, and ``GroupID`` links to the resolved
    people_groups entity once resolution has run.
    """

    unit: str
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None

    # (A) Link to the resolved people_groups entity (ULID), when known. Empty until
    # unit entity-resolution runs; present = authoritative person->unit affiliation.
    GroupID: Optional[str] = Field(
        default=None, description="26-char ULID of the people_groups unit"
    )
    # (B) Structured, normalized unit identity — usable immediately for unit-scoped
    # sourcing even before GroupID resolution:
    designation: Optional[str] = Field(
        default=None, description="Canonical designation, e.g. '9th Infantry Division'"
    )
    echelon: Optional[str] = Field(
        default=None,
        description="Echelon type: division|corps|army|regiment|battalion|company|brigade|group|fleet|squadron|wing",
    )
    unit_number: Optional[str] = Field(
        default=None,
        description="Numeric designator, e.g. '9' for 9th Infantry Division",
    )

    model_config = ConfigDict(populate_by_name=True)


class GroupAffiliation(BaseModel):
    """A person's membership in a people_group that is NOT a military unit — e.g. a
    legislative body (House/Senate), a state/constituency, or a political party.

    Civilians (and officials) can belong to several groups concurrently
    ("Representative John Smith from NJ, a Republican" → House + New Jersey + Republican
    Party). Such memberships are often **implied by a title/honorific**, which is a
    POINT-IN-TIME attribute: a later honorific ("Senator Smith") does NOT prove the
    person held that role in the source's timeframe. Memberships derived from a title
    are therefore born **date-unverified** and must be confirmed by a deliberate
    temporal-validation step before being asserted as fact or trusted as strong
    dedup evidence. See docs/current/features/people/biographical-enrichment.md.
    """

    group: str  # free-text group name ("House of Representatives", "New Jersey", "Republican Party")
    GroupID: Optional[str] = Field(
        default=None, description="26-char ULID of the resolved people_groups entity"
    )
    group_kind: Optional[str] = Field(
        default=None,
        description="legislature|state|party|executive|judiciary|civilian_org",
    )
    # --- Temporal provenance (the honorific-is-point-in-time guard) ---
    implied_from_title: Optional[bool] = Field(
        default=None,
        description="True if derived from a title/honorific/alias rather than stated membership",
    )
    date_verified: Optional[bool] = Field(
        default=None,
        description=(
            "True ONLY after temporal validation confirmed the membership held in the "
            "source timeframe. False/None = NOT yet confirmed — must not be asserted as "
            "fact; weak dedup evidence only."
        ),
    )
    as_of_source_date: Optional[str] = Field(
        default=None,
        description="The source document's date/year the implied membership is 'as of'",
    )
    source_title: Optional[str] = Field(
        default=None, description="The title/honorific the membership was implied from"
    )

    model_config = ConfigDict(populate_by_name=True)


class Education(BaseModel):
    """Educational institution attended by a person."""

    institution: str
    degree: Optional[str] = None
    year: Optional[str] = None


class Family(BaseModel):
    """Family information for a person."""

    spouse: Optional[str] = None
    children: list[str] = Field(default_factory=list)


class BiographySource(BaseModel):
    """Source of biographical information."""

    source: str
    page: Optional[str] = None  # Can be single page or range (e.g., "16" or "16-17")
    confidence: Optional[float] = None
    fields_sourced: list[str] = Field(
        default_factory=list,
        description="List of biographical fields sourced from this reference",
    )
    # Provenance links (schema 2.25): the sub-event this bio fact came from (filled at
    # extraction) + the backing bibliography entry (resolved in Phase 3 from the footnote).
    EventID: Optional[str] = None
    Sub_eventID: Optional[str] = None
    BibliographyID: Optional[str] = None

    model_config = {"populate_by_name": True}

    @field_validator("page", mode="before")
    @classmethod
    def convert_page_to_string(cls, v):
        """Convert page to string if it's an int."""
        if v is None:
            return None
        return str(v)


class BiographicalProfile(BaseModel):
    """Biographical profile information for a person."""

    birth_date: Optional[str] = None
    birth_place: Optional[str] = None
    death_date: Optional[str] = None
    death_place: Optional[str] = None
    nationality: Optional[str] = Field(
        default=None,
        description=(
            "ISO 3166-1 alpha-3 country code of the person's nationality/citizenship "
            "(e.g. 'USA', 'GBR', 'DEU', 'FRA', 'CAN'). Optional: often not stated in "
            "narrative/OOB sources and may be inferred by enrichment (unit, award, "
            "source) rather than extracted. Do not fabricate at extraction time."
        ),
    )
    nationality_served: Optional[str] = Field(
        default=None,
        description=(
            "ISO 3166-1 alpha-3 code of the POWER the person served under, when it "
            "differs from `nationality` (e.g. a French national in the Waffen-SS = "
            "FRA nationality, DEU served; a Pole in the RAF = POL/GBR). Optional; "
            "leave null when it equals `nationality`."
        ),
    )
    role_type: Optional[str] = None
    primary_group_id: Optional[str] = Field(
        default=None,
        description=(
            "26-char ULID of the person's PRIMARY people_groups unit — a derived, "
            "point-in-time convenience pointer (populated by resolution/dedup, not "
            "extraction). The authoritative, time-aware affiliations remain the "
            "per-unit `units_served[].GroupID` links; this is a shortcut to the "
            "principal one and must never be the sole source of a person->unit link."
        ),
    )
    ranks: list[MilitaryRank] = Field(default_factory=list)
    units_served: list[UnitServed] = Field(default_factory=list)
    group_affiliations: list[GroupAffiliation] = Field(
        default_factory=list,
        description=(
            "Non-unit group memberships (legislature/state/party/etc), possibly "
            "concurrent. Title-implied memberships are date-unverified until a "
            "temporal-validation step confirms them."
        ),
    )
    education: list[Education] = Field(default_factory=list)
    military_awards: list[MilitaryAward] = Field(default_factory=list)
    family: Optional[Family] = None
    aliases: list[str] = Field(default_factory=list)
    biography_sources: list[BiographySource] = Field(default_factory=list)
    biographical_details: Optional[str] = None


class PersonEventMention(BaseModel):
    """Event mention linking a person to a specific event."""

    MentionID: Optional[str] = Field(
        default=None,
        description="server-minted (required on the stored record); LLM must NOT supply it",
    )
    Event_Name: str
    EventID: str = Field(description="26-character ULID")
    Sub_event_Name: str = Field(alias="Sub-event_Name")
    Sub_eventID: str = Field(description="26-character ULID", alias="Sub-eventID")
    book: Optional[str] = None
    author: Optional[str] = None
    series: Optional[str] = None
    date: Optional[str] = None
    DateMentionID: Optional[str] = None
    position_at_event: Optional[str] = None
    life_event: Optional[str] = None
    original_text: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class Person(BaseModel):
    """Person entity with biographical information and event mentions."""

    PersonID: Optional[str] = Field(
        default=None,
        description="server-minted (required on the stored record); LLM must NOT supply it",
    )
    name: str
    source_language: str = "English"
    biographical_profile: Optional[BiographicalProfile] = None
    event_mentions: list[PersonEventMention] = Field(default_factory=list)


class PeopleOutput(BaseModel):
    """Output container for extracted people entities."""

    People: list[Person]


def create_people_prompt(
    sub_event: Dict[str, Any],
    event_id: str,
    event_name: str,
    book: str = "",
    author: str = "",
    series: str = "",
    empty_counter: Optional[dict] = None,
) -> str:
    """Create prompt for people extraction from a sub-event."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    sub_event_summary = sub_event.get("Sub-event_summary", "")
    fulltext = sub_event.get("Sub-event_fulltext", {})

    text_parts = []
    for key in sorted(fulltext.keys()):
        text_parts.append(fulltext[key])
    text = "\n\n".join(text_parts)

    if not text.strip():
        note_empty_fulltext_skip("people", sub_event_id, empty_counter)
        return ""

    from src.utils.prompt_loader import get_system_prompt, render_prompt

    prompt = render_prompt(
        "people",
        book=book,
        author=author,
        series=series,
        event_name=event_name,
        event_id=event_id,
        sub_event_summary=sub_event_summary,
        sub_event_id=sub_event_id,
        text=text,
    )

    return prompt


@lru_cache(maxsize=5000)
def _normalize_name(name: str) -> str:
    """Normalize person name for matching (deprecated - use text_utils.normalize_name)."""
    return normalize_name(name)


def _name_to_filename(name: str, person_id: str) -> str:
    """Convert person name to safe filename."""
    # Remove special characters, keep alphanumeric and spaces
    safe_name = _SPECIAL_CHARS_PATTERN.sub("", name)
    # Replace spaces with underscores
    safe_name = _WHITESPACE_PATTERN.sub("_", safe_name)
    # Limit length and add ULID suffix for uniqueness
    safe_name = safe_name[:50]
    return f"{safe_name}_{person_id[:12]}.json"


def _load_person_file(person_file: Path) -> Dict[str, Any]:
    """Load a person file."""
    with open(person_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_person_file(person_file: Path, person_data: Dict[str, Any]) -> None:
    """Save a person file (via write_json_with_lock: cross-host flock + dual-write
    to DynamoEntityStore — #8, required for correct isolation under concurrency)."""
    from src.utils.file_lock import write_json_with_lock

    write_json_with_lock(person_file, person_data)


def backfill_people_identity(people_dir: Path) -> tuple:
    """Repair EXISTING person records missing `name`/`PersonID` in place (MED fix companion).

    Legacy records (pre-HIGH#7 server-side mint) were persisted without a top-level name or
    PersonID — a commander-level entity with no primary key won't resolve in any cross-reference.
    Recover `name` from the filename stem (which IS the normalized name) and MINT a PersonID. Writes
    only records that were missing a field, via the locked save path. Returns (scanned, fixed).
    """
    import glob as _glob
    from src.utils.file_lock import write_json_with_lock

    scanned = fixed = blocked = 0
    for f in _glob.glob(str(people_dir / "*.json")):
        name = Path(f).stem
        if name in ("index", "duplicate_report") or name.startswith("."):
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                rec = json.load(fh)
            if not isinstance(rec, dict):
                continue
            scanned += 1
            changed = False
            if not rec.get("name"):
                rec["name"] = name
                changed = True
                logger.info("OBSERV person.repaired field=name name=%s", name)
            if not rec.get("PersonID"):
                rec["PersonID"] = str(ulid.new())
                changed = True
                logger.info("OBSERV person.repaired field=PersonID name=%s", name)
            if changed:
                # entity="people" stamps the correct schema version; honor the return so a
                # schema-BLOCKED write is NOT counted as fixed (would leave a still-broken record).
                if write_json_with_lock(Path(f), rec, entity="people"):
                    fixed += 1
                else:
                    blocked += 1
                    logger.warning(
                        "OBSERV person.repair_blocked name=%s (schema guard rejected write)",
                        name,
                    )
        except Exception as e:  # noqa: BLE001 - per-file best-effort
            logger.debug("people identity backfill skipped %s: %s", f, e)
    logger.info(
        "OBSERV person.identity_backfill scanned=%d fixed=%d blocked=%d",
        scanned,
        fixed,
        blocked,
    )
    return scanned, fixed


def _update_index(index_file: Path, name: str, filename: str) -> None:
    """Update the people index atomically."""
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = {}

    name_key = _normalize_name(name)
    index[name_key] = filename

    # Atomic write: write to temp file, then rename
    temp_file = index_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    temp_file.replace(index_file)


def _merge_list_field(existing: list, new: list, dedupe_func=None) -> list:
    """Merge list fields with optional deduplication function."""
    if dedupe_func:
        return dedupe_func(existing + new)
    # JSON-based deduplication for other fields
    item_set = {json.dumps(item, sort_keys=True) for item in existing}
    for item in new:
        item_json = json.dumps(item, sort_keys=True)
        if item_json not in item_set:
            existing.append(item)
    return existing


def _merge_family(
    existing_family: Dict[str, Any], new_family: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge family information."""
    if not existing_family:
        return new_family
    if not new_family:
        return existing_family

    if not existing_family.get("spouse") and new_family.get("spouse"):
        existing_family["spouse"] = new_family["spouse"]

    existing_children = existing_family.get("children", [])
    new_children = new_family.get("children", [])
    child_set = set(existing_children)
    for child in new_children:
        if child not in child_set:
            existing_children.append(child)
    existing_family["children"] = existing_children
    return existing_family


def _update_missing_fields(
    existing_bio: Dict[str, Any], new_bio: Dict[str, Any]
) -> None:
    """Update biographical fields if new data exists and old doesn't."""
    for field in [
        "birth_date",
        "birth_place",
        "death_date",
        "death_place",
        "nationality",
        "nationality_served",
        "role_type",
        "primary_group_id",
        "biographical_details",
    ]:
        if new_bio.get(field) and not existing_bio.get(field):
            existing_bio[field] = new_bio[field]


def _merge_event_mentions(existing: Dict[str, Any], new_person: Dict[str, Any]) -> None:
    """Append new event mentions onto ``existing`` in place, deduplicating by Sub_eventID."""
    existing_mentions = existing.get("event_mentions", [])
    new_mentions = new_person.get("event_mentions", [])
    seen_sub_events = {
        m.get("Sub_eventID") for m in existing_mentions if m.get("Sub_eventID")
    }
    for m in new_mentions:
        sub_id = m.get("Sub_eventID")
        if not sub_id or sub_id not in seen_sub_events:
            existing_mentions.append(m)
            if sub_id:
                seen_sub_events.add(sub_id)
    existing["event_mentions"] = existing_mentions


def _merge_bio_lists(existing_bio: Dict[str, Any], new_bio: Dict[str, Any]) -> None:
    """Merge the deduplicated list fields + family + missing scalars of a biographical
    profile, mutating ``existing_bio`` in place (both bios known to be present)."""
    existing_bio["military_awards"] = _merge_list_field(
        existing_bio.get("military_awards", []),
        new_bio.get("military_awards", []),
        _deduplicate_awards,
    )
    existing_bio["ranks"] = _merge_list_field(
        existing_bio.get("ranks", []), new_bio.get("ranks", []), _deduplicate_ranks
    )
    existing_bio["units_served"] = _merge_list_field(
        existing_bio.get("units_served", []),
        new_bio.get("units_served", []),
        _deduplicate_units,
    )
    existing_bio["education"] = _merge_list_field(
        existing_bio.get("education", []), new_bio.get("education", [])
    )
    existing_bio["aliases"] = _merge_list_field(
        existing_bio.get("aliases", []), new_bio.get("aliases", [])
    )
    existing_bio["biography_sources"] = _merge_list_field(
        existing_bio.get("biography_sources", []),
        new_bio.get("biography_sources", []),
    )

    # Merge family
    new_family = new_bio.get("family")
    if new_family:
        existing_family = existing_bio.get("family", {})
        existing_bio["family"] = _merge_family(existing_family, new_family)

    # Update missing fields
    _update_missing_fields(existing_bio, new_bio)


def _merge_person(
    existing: Dict[str, Any], new_person: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge new person data into existing person record."""
    # Add new event mentions (deduplicate by Sub_eventID)
    _merge_event_mentions(existing, new_person)

    # Merge biographical profile if new data is more complete
    new_bio = new_person.get("biographical_profile")
    existing_bio = existing.get("biographical_profile")

    if new_bio and existing_bio:
        _merge_bio_lists(existing_bio, new_bio)
    elif new_bio:
        existing["biographical_profile"] = new_bio

    return existing


def _check_if_processed(event_file: Path, people_dir: Path) -> bool:
    """Check if event file has already been processed. Returns True if processed."""
    processed_registry = people_dir / ".processed_events.json"
    event_file_str = event_file.name

    if processed_registry.exists():
        with open(processed_registry, "r", encoding="utf-8") as f:
            processed = json.load(f)
        if event_file_str in processed:
            from src.utils.config import should_reprocess

            if not should_reprocess("people"):
                logger.info(
                    f"Event already processed for people extraction: {event_file.name}"
                )
                return True

    return False


def _load_book_metadata(event_file: Path) -> tuple[str, str, str]:
    """Load book metadata from parsed file. Returns (book, author, series)."""
    parsed_file = event_file.parent / event_file.name.replace(
        "-event.json", "-parsed.json"
    )

    if not parsed_file.exists():
        return "", "", ""

    with open(parsed_file, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)
        if not parsed_data:
            return "", "", ""

        return (
            parsed_data.get("book", ""),
            parsed_data.get("author", ""),
            parsed_data.get("series", ""),
        )


def _load_people_index(index_file: Path) -> dict:
    """Load people index from file."""
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_people_index(index_file: Path, index: dict) -> None:
    """Save people index atomically."""
    temp_file = index_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    temp_file.replace(index_file)


# Phase-2 people-enrichment coverage counters (observability). A wholesale no-op (0 of N people
# enriched) previously produced ZERO aggregate signal — only per-person lines when the path ran —
# so an enrichment regression was invisible. Emitted as an `enrich.summary` rollup per run with a
# 0-of-N anomaly warning. Both the sync (_process_person) and batch (_enrich_person_batch_apply)
# paths funnel through _enrich_person_phase2, so counting there covers both.
#
# TASK-LOCAL via ContextVar (mirrors grok_client.current_book): chapters run CONCURRENTLY
# (asyncio.gather, max_parallel=3), so a shared module dict would interleave — one chapter's reset
# zeroing another's in-flight counts. A ContextVar gives each async task (and the sync path) its own
# counter dict, so resets/summaries never cross chapters.
_enrich_counts: ContextVar[Dict[str, int]] = ContextVar("enrich_counts")


def _reset_enrich_counts() -> None:
    _enrich_counts.set(
        {"total": 0, "enriched": 0, "skipped": 0, "collecting": 0, "errors": 0}
    )


def _bump_enrich(key: str) -> None:
    """Increment a task-local enrichment counter (fail-safe: a missing/unset context is a no-op)."""
    c = _enrich_counts.get(None)
    if c is not None and key in c:
        c[key] += 1


def _emit_enrich_summary(path: str) -> None:
    """Emit the phase-level people-enrichment coverage rollup + a 0-of-N anomaly warning."""
    c = _enrich_counts.get(None)
    if c is None:  # summary called without a prior reset — nothing to report
        return
    logger.info(
        "OBSERV enrich.summary entity=person path=%s total=%d enriched=%d skipped_already=%d "
        "collecting=%d errors=%d",
        path,
        c["total"],
        c["enriched"],
        c["skipped"],
        c["collecting"],
        c["errors"],
    )
    # Anomaly: people were extracted but NONE enriched and NONE merely collecting/skipped-as-fresh
    # -> the enrichment path is effectively dead (the class of bug that hid before). Flag loudly.
    if c["total"] > 0 and c["enriched"] == 0 and c["collecting"] == 0:
        logger.warning(
            "OBSERV enrich.anomaly entity=person path=%s: %d people extracted but 0 enriched "
            "(0 collecting) — enrichment may be disabled/broken (fix upstream)",
            path,
            c["total"],
        )


def _enrich_person_phase2(person_data: dict, grok_client) -> None:
    """Phase-2 Grokipedia/Wikipedia enrichment of a person dict IN PLACE, before the
    native people save path commits it. Fail-safe: a fetch error (or batch-mode
    collection) must never abort extraction — log + continue so the un-enriched record
    still saves and Phase 2 proceeds."""
    if grok_client is None:
        return
    _bump_enrich("total")
    try:
        from src.extraction.enrich_biographies import enrich_person_from_sources

        enriched_now = enrich_person_from_sources(person_data, grok_client)
        _bump_enrich("enriched" if enriched_now else "skipped")
        # enrich.persisted counterpart to enrich.collecting (symmetry asked for): make the RETRIEVE
        # outcome visible. enriched_now distinguishes "this call populated it" from a short-circuit
        # (already-enriched / fresh not-found); has_bio=0 means fetched/returned but no profile.
        bio = person_data.get("biographical_profile") or {}
        n_sources = len(bio.get("biography_sources", []) or []) if bio else 0
        logger.info(
            "OBSERV enrich.persisted entity=person name=%s enriched_now=%d has_bio=%d sources=%d",
            person_data.get("name", "?"),
            1 if enriched_now else 0,
            1 if bio else 0,
            n_sources,
        )
    except BatchModeCollecting:
        # Batch COLLECT pass: the enrichment request was queued, not answered. MUST propagate so the
        # person is NOT saved as "done"/un-enriched — the RETRIEVE pass then applies the batch result
        # and persists biographical_profile. Swallowing this (broad except below) was why people bio
        # enrichment was 0/N (same class as the optional-entity-zero bug).
        _bump_enrich("collecting")
        logger.info(
            "OBSERV enrich.collecting entity=person name=%s",
            person_data.get("name", "?"),
        )
        raise
    except (
        Exception
    ) as e:  # noqa: BLE001 - enrichment is additive; never block extraction
        _bump_enrich("errors")
        logger.warning(
            "  Phase-2 enrichment skipped for %s: %s",
            person_data.get("name", "?"),
            e,
        )


def _enrich_person_batch_apply(
    entity_file: Path, grok_client: Optional[GrokClient]
) -> None:
    """Phase-2 Grokipedia/Wikipedia enrichment for a person FINALIZED on the batch
    RETRIEVE/apply path (batch_parallel._process_entity_obj). The generic batch path
    creates/saves the person record without enrichment — unlike the synchronous
    ``_process_person`` path which enriches via ``_enrich_person_phase2`` before save.
    This re-reads the just-persisted record under the central write guard, enriches it
    IN PLACE (reusing ``_enrich_person_phase2`` / ``enrich_person_from_sources`` — no
    duplicated logic, same ``_is_already_enriched`` gate so a person is never enriched
    twice), re-validates, and commits via ``write_json_with_lock``.

    Fail-safe: any enrichment error (including a batch-mode collect) must NEVER abort
    extraction — the already-saved (un-enriched) record is left intact.
    """
    if grok_client is None:
        return
    from src.utils.file_lock import locked_json
    from src.schemas.schema_contract import read_for_update

    try:
        with locked_json(entity_file) as (data, save):
            data, skip = read_for_update(data, SCHEMA_TARGET, logger, entity_file.name)
            if skip or not data.get("name"):
                return
            # _enrich_person_phase2 is itself fail-safe (swallows fetch errors) and
            # re-raises BatchModeCollecting; catch that here so a collect never aborts
            # the batch apply — the un-enriched record stays saved.
            try:
                _enrich_person_phase2(data, grok_client)
            except BatchModeCollecting:
                logger.info(
                    "OBSERV enrich.collecting entity=person path=batch name=%s",
                    data.get("name", "?"),
                )
                return
            Person(**data)  # Validate before committing the enriched record.
            save(data)
    except (
        Exception
    ) as e:  # noqa: BLE001 - enrichment is additive; never block extraction
        logger.warning(
            "  Phase-2 batch enrichment skipped for %s: %s",
            entity_file.name,
            e,
        )


def _mint_person_ids(person: dict, person_id: str) -> None:
    """Mint server-side PersonID + per-mention MentionIDs (HIGH#7a), logging when an LLM-supplied
    value was replaced. Mutates ``person`` in place."""
    llm_person_id = person.get("PersonID")
    person["PersonID"] = person_id
    if llm_person_id and llm_person_id != person_id:
        logger.info(
            "OBSERV id.minted entity=person field=PersonID replaced_llm_value=1"
        )
    # HIGH#7a: mint the per-mention MentionID server-side too (consistent with places/dates/weather).
    for _m in person.get("event_mentions", []) or []:
        if isinstance(_m, dict):
            _llm_mid = _m.get("MentionID")
            _m["MentionID"] = str(ulid.new())
            if _llm_mid and _llm_mid != _m["MentionID"]:
                logger.info(
                    "OBSERV id.minted entity=person field=MentionID replaced_llm_value=1"
                )


def _update_existing_person(
    person_file: Path,
    person: dict,
    name: str,
    person_id: str,
    grok_client: Optional[GrokClient],
) -> None:
    """Merge ``person`` into the existing locked record, backfill integrity fields, enrich,
    validate, and save (native people save path)."""
    from src.utils.file_lock import locked_json
    from src.schemas.schema_contract import read_for_update

    with locked_json(person_file) as (existing_person, save):
        # Read the historical record under this module's schema contract: a
        # FUTURE-schema record is skipped gracefully (code not built for it);
        # older records merge best-effort (upgrade case-by-case if registered).
        existing_person, skip = read_for_update(
            existing_person, SCHEMA_TARGET, logger, name
        )
        if not skip:
            merged = _merge_person(existing_person, person)
            # Integrity guard (MED fix): a person record MUST have name + PersonID. Legacy
            # records (pre-HIGH#7) and the merge path could leave the survivor without them
            # (merge keeps `existing`, which may predate the server-side mint). Backfill from
            # the known name + minted id so no nameless/ID-less person persists.
            if not merged.get("name"):
                merged["name"] = name
                logger.info("OBSERV person.repaired field=name name=%s", name)
            if not merged.get("PersonID"):
                merged["PersonID"] = person_id
                logger.info("OBSERV person.repaired field=PersonID name=%s", name)
            # Phase-2 Grokipedia/Wikipedia enrichment on the merged dict BEFORE
            # save, so the native people save path commits the enriched record.
            _enrich_person_phase2(merged, grok_client)
            Person(**merged)  # Validate
            save(merged)


def _create_new_person(
    person: dict,
    people_dir: Path,
    index: dict,
    name: str,
    name_key: str,
    person_id: str,
    grok_client: Optional[GrokClient],
) -> None:
    """Create a brand-new person file + index entry (enrich before save). Mutates ``index``."""
    filename = _name_to_filename(name, person_id)
    person_file = people_dir / filename
    _enrich_person_phase2(person, grok_client)
    _save_person_file(person_file, person)
    index[name_key] = filename
    logger.debug("  Created: %s", name)


def _process_person(
    person: dict,
    people_dir: Path,
    index: dict,
    grok_client: Optional[GrokClient] = None,
) -> tuple[bool, bool]:
    """Process a single person. Returns (is_new, is_updated)."""
    name = person["name"]
    # HIGH#7a: PersonID is a NEW entity id — MINT it server-side, never trust the LLM's value.
    # (A PersonID is not embedded in the prompt as a reference, so there is nothing to honor; a
    # hallucinated/colliding LLM id would otherwise become a cross-reference target.)
    person_id = str(ulid.new())
    _mint_person_ids(person, person_id)
    name_key = _normalize_name(name)

    # Check in-memory index for existing person
    existing_filename = index.get(name_key)

    if existing_filename:
        # Update existing person
        person_file = people_dir / existing_filename
        if person_file.exists():
            _update_existing_person(person_file, person, name, person_id, grok_client)
            logger.debug("  Updated: %s", name)
            return False, True
        else:
            # Index points to missing file, create new
            _create_new_person(
                person, people_dir, index, name, name_key, person_id, grok_client
            )
            return True, False
    else:
        # New person
        _create_new_person(
            person, people_dir, index, name, name_key, person_id, grok_client
        )
        return True, False


def _extract_people_for_sub_event(
    sub_event: dict,
    event_id: str,
    event_name: str,
    book: str,
    author: str,
    series: str,
    grok_client: GrokClient,
    max_retries: int,
    empty_counter: Optional[dict] = None,
) -> Optional[list]:
    """Extract people for a single sub-event with retry logic."""
    sub_event_id = sub_event.get("Sub-eventID", "")
    logger.info("  Processing sub-event %s", sub_event_id)

    prompt = create_people_prompt(
        sub_event, event_id, event_name, book, author, series, empty_counter
    )
    if not prompt:
        return []

    for attempt in range(max_retries):
        try:
            people_output = grok_client.extract_structured(
                prompt=prompt,
                schema=PeopleOutput,
                system_prompt=get_system_prompt("people"),
                use_cache=(attempt == 0),
                cache_type="people",
            )

            people_dict: Dict[str, Any] = people_output.model_dump(by_alias=True)
            people_dict = _fix_invalid_ulids(people_dict)  # type: ignore

            # HIGH#7b: verify Grok honored the embedded Sub-eventID echo (observability only).
            from src.utils.deterministic_ids import observe_embedded_ulid

            observe_embedded_ulid(
                sub_event_id, people_dict.get("Sub-eventID"), "people"
            )

            extracted_people = people_dict.get("People", [])
            logger.info("  ✓ Processed %d people", len(extracted_people))
            return extracted_people

        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as e:
            if attempt < max_retries - 1:
                logger.warning(f"  ⚠ Attempt {attempt + 1} failed: {e}")
                logger.info(f"  Retrying ({attempt + 2}/{max_retries})...")
            else:
                import os

                if os.environ.get("PIPELINE_PHASE"):
                    logger.info("  ⊘ Sync people fallback skipped (batch mode): %s", e)
                else:
                    logger.error(f"  ✗ All {max_retries} attempts failed: {e}")
                return None

    return None


def _mark_event_processed(
    event_file: Path,
    people_dir: Path,
    event_data: dict,
    new_people_count: int,
    updated_people_count: int,
) -> None:
    """Mark event file as processed in registry."""
    processed_registry = people_dir / ".processed_events.json"
    event_file_str = event_file.name

    if processed_registry.exists():
        with open(processed_registry, "r", encoding="utf-8") as f:
            processed = json.load(f)
    else:
        processed = {}

    processed[event_file_str] = {
        "processed_at": event_data.get("extracted_date", ""),
        "new_people": new_people_count,
        "updated_people": updated_people_count,
    }

    with open(processed_registry, "w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2)


def _record_recall_gaps_safe(
    people_dir: Path,
    event_id: str,
    sub_event_id: str,
    sub_event: dict,
    extracted_people: Optional[list],
) -> None:
    """Fail-safe wrapper: detect+queue people recall-gaps for one sub-event. Never raises."""
    try:
        text = _text_from_sub_event(sub_event)
        if not text.strip():
            return
        record_people_recall_gaps(
            people_dir, event_id, sub_event_id, text, list(extracted_people or [])
        )
    except Exception as e:  # noqa: BLE001 - recall-gap review must never fail the run
        logger.debug("recall-gap review skipped for %s: %s", sub_event_id, e)


def extract_people(
    event_file: Path,
    grok_client: GrokClient,
    output_dir: Path,
    max_retries: int = 3,
) -> Optional[Path]:
    """
    Extract people from event file and save as individual files.

    Args:
        event_file: Path to event JSON file
        grok_client: Grok API client
        output_dir: Root output directory (contains all books)

    Returns:
        Path to people directory, or None if failed
    """
    # People directory - individual files per person
    people_dir = output_dir / "people"
    people_dir.mkdir(parents=True, exist_ok=True)

    # Check if already processed
    if _check_if_processed(event_file, people_dir):
        return people_dir

    _reset_enrich_counts()  # per-event people-enrichment coverage (summary emitted before return)

    with open(event_file, "r", encoding="utf-8") as f:
        event_data = json.load(f)

    # Get book metadata
    book, author, series = _load_book_metadata(event_file)

    event_name = event_data.get("Chapter", "")
    event_obj = event_data.get("Event", {})
    event_id = event_obj.get("EventID", "")
    sub_events = event_obj.get("Sub-events", [])

    new_people_count = 0
    updated_people_count = 0

    # Load index
    index_file = people_dir / "index.json"
    index = _load_people_index(index_file)

    empty_counter: dict = {"empty": 0}

    for sub_event in sub_events:
        sub_event_id = sub_event.get("Sub-eventID", "")
        extracted_people = _extract_people_for_sub_event(
            sub_event,
            event_id,
            event_name,
            book,
            author,
            series,
            grok_client,
            max_retries,
            empty_counter,
        )

        # RECALL-GAP (fail-safe): surface rank-anchored names in the source that extraction missed.
        _record_recall_gaps_safe(
            people_dir, event_id, sub_event_id, sub_event, extracted_people
        )

        if not extracted_people:
            continue

        # Process each person
        for person in extracted_people:
            is_new, is_updated = _process_person(person, people_dir, index, grok_client)
            if is_new:
                new_people_count += 1
            if is_updated:
                updated_people_count += 1

    # Save index
    _save_people_index(index_file, index)

    _emit_enrich_summary("sync")

    # Mark as processed
    _mark_event_processed(
        event_file, people_dir, event_data, new_people_count, updated_people_count
    )

    logger.info(
        "People directory: %d new, %d updated", new_people_count, updated_people_count
    )
    observe_yield(
        "people",
        event_id or "?",
        len(sub_events),
        count_nonempty_fulltext(event_data),
        new_people_count + updated_people_count,
    )
    warn_all_subevents_empty(
        "people", event_id or "?", len(sub_events), int(empty_counter.get("empty", 0))
    )

    return people_dir
