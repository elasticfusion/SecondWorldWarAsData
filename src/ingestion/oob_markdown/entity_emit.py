"""Converge OOB command-staff rows into the people entity space.

This is the step that makes the OOB crosswalk *matter*: it consumes the
resolved :class:`~src.ingestion.oob_markdown.crosswalk.CrosswalkLink` records and
emits/merges them into ``output/people/`` so the existing dedup pass
(``scripts/find_duplicate_people.py`` + the dedup review UI) unifies OOB-derived
people with narrative-derived people into single ``PersonID``\\s — the "Huebner
goal".

Design choices (see docs/current/dataquality/INGESTION_FRONT_END.md):

* **OOB data is biographical, not narrative.** A command-staff row ("Maj Gen
  Clarence R Huebner, Comdg Gen, 1st Infantry Division, 15 Oct 1943") maps to
  ``biographical_profile`` (``ranks``/``units_served``/``biography_sources``),
  **not** to ``event_mentions``. Event mentions require narrative
  ``EventID``/``Sub-eventID`` ULIDs that OOB rows do not have; synthesizing fake
  ones would pollute the narrative event graph. So OOB never fabricates events.

* **Match tier drives convergence** (mirrors the crosswalk's safety model):
    - ``exact``  -> the row is already resolved to a real ``PersonID``; merge the
      OOB bio into that existing person file. Safe: auto-confirmed.
    - ``fuzzy``  -> the match is a *candidate* pending human review. Do NOT
      silently merge. Mint a NEW person (tagged with the fuzzy candidate) so the
      standard dedup pass surfaces it and a reviewer confirms/rejects the merge.
    - ``none``   -> mint a NEW person so dedup can later catch it against a
      narrative person of the same name.

* **Provenance is explicit.** Every emitted/merged person records an OOB
  ``BiographySource`` (``source="OOB: <file>"``) so OOB-derived data is auditable
  and distinguishable from narrative-derived data.

* **Reuses the real write path.** People files are written via
  ``write_json_with_lock`` (metadata stamp + schema validation + DynamoDB
  dual-write) and merged via ``src.extraction.people._merge_person`` — no
  parallel logic. The ``output/people/index.json`` name->filename map is kept in
  sync via ``_update_index``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import ulid

from src.extraction.people import (
    _merge_person,
    _name_to_filename,
    _update_index,
)
from src.ingestion.oob_markdown.crosswalk import (
    MATCH_EXACT,
    MATCH_FUZZY,
    CrosswalkResult,
)
from src.utils.file_lock import write_json_with_lock
from src.utils.text_utils import normalize_name

logger = logging.getLogger(__name__)

# Emit outcome tags.
EMIT_MERGED = "merged"  # bio merged into an existing (exact-matched) PersonID
EMIT_CREATED = "created"  # new person minted (fuzzy candidate or no match)
EMIT_SKIPPED = "skipped"  # nothing to emit (e.g. blank name)


@dataclass
class EmitResult:
    """Summary of an OOB->people convergence pass over one file's links."""

    merged: int = 0
    created: int = 0
    skipped: int = 0

    def record(self, outcome: str) -> None:
        """Tally one link outcome."""
        if outcome == EMIT_MERGED:
            self.merged += 1
        elif outcome == EMIT_CREATED:
            self.created += 1
        else:
            self.skipped += 1

    def to_dict(self) -> Dict[str, int]:
        """Return a JSON-serializable summary."""
        return {"merged": self.merged, "created": self.created, "skipped": self.skipped}


def _oob_biographical_profile(link: Any) -> Dict[str, Any]:
    """Build a ``biographical_profile`` dict from one crosswalk link/row.

    Maps rank -> ranks[], division -> units_served[], and always records an
    OOB provenance ``biography_sources`` entry naming the source file.
    """
    ranks: List[Dict[str, Any]] = []
    if link.rank:
        ranks.append({"rank": link.rank})

    units: List[Dict[str, Any]] = []
    if link.division:
        units.append({"unit": link.division})

    fields_sourced = [
        f for f, v in (("ranks", link.rank), ("units_served", link.division)) if v
    ]
    source_note = f"OOB: {link.source_file}"
    if link.position:
        source_note = f"{source_note} ({link.position})"

    return {
        "ranks": ranks,
        "units_served": units,
        "biography_sources": [
            {
                "source": source_note,
                "confidence": link.confidence or None,
                "fields_sourced": fields_sourced,
            }
        ],
    }


def _new_person_dict(link: Any, notes: Optional[str] = None) -> Dict[str, Any]:
    """Mint a new ``Person``-shaped dict from a crosswalk link/row."""
    person: Dict[str, Any] = {
        "PersonID": str(ulid.new()),
        "name": link.name,
        "source_language": "English",
        "biographical_profile": _oob_biographical_profile(link),
        "event_mentions": [],
    }
    if notes:
        person["oob_convergence_note"] = notes
    return person


class PeopleEmitter:  # pylint: disable=too-few-public-methods
    """Emit/merge OOB crosswalk links into ``output/people/``.

    Stateful: loads ``index.json`` once and keeps it in sync across ``emit``
    calls so repeated/streamed convergence stays consistent.
    """

    def __init__(self, people_dir: Path):
        self.people_dir = people_dir
        self.index_file = people_dir / "index.json"
        # name_key -> filename, loaded once and kept in sync.
        self._index: Dict[str, str] = self._load_index()

    def _load_index(self) -> Dict[str, str]:
        if self.index_file.exists():
            try:
                return json.loads(self.index_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def emit(self, result: CrosswalkResult) -> EmitResult:
        """Converge every link in ``result`` into the people store."""
        self.people_dir.mkdir(parents=True, exist_ok=True)
        summary = EmitResult()
        for link in result.links:
            summary.record(self._emit_link(link))
        logger.info(
            "OOB->people convergence: %d merged, %d created, %d skipped",
            summary.merged,
            summary.created,
            summary.skipped,
        )
        return summary

    def _emit_link(self, link: Any) -> str:
        """Converge one link; returns an EMIT_* outcome."""
        if not link.name or not link.name.strip():
            return EMIT_SKIPPED

        if link.match_method == MATCH_EXACT and link.person_id:
            return self._merge_into_existing(link)

        # fuzzy candidate or no match -> mint a new person for dedup to unify.
        note = None
        if link.match_method == MATCH_FUZZY and link.matched_name:
            note = (
                f"OOB fuzzy candidate for existing '{link.matched_name}' "
                f"({link.confidence:.0%}); pending dedup review"
            )
        return self._create_new(link, note)

    def _merge_into_existing(self, link: Any) -> str:
        """Merge OOB bio into the already-resolved PersonID file (exact match)."""
        filename = self._find_file_for_person(link.person_id, link.matched_name)
        if not filename:
            # Resolved id but file not locatable (e.g. index-only) -> create.
            return self._create_new(link, None)
        person_file = self.people_dir / filename
        try:
            existing = json.loads(person_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._create_new(link, None)

        oob_person = {"biographical_profile": _oob_biographical_profile(link)}
        merged = _merge_person(existing, oob_person)
        write_json_with_lock(person_file, merged)
        return EMIT_MERGED

    def _create_new(self, link: Any, note: Optional[str]) -> str:
        """Mint and persist a new person; keep the index in sync."""
        person = _new_person_dict(link, note)
        filename = _name_to_filename(person["name"], person["PersonID"])
        person_file = self.people_dir / filename
        write_json_with_lock(person_file, person)
        _update_index(self.index_file, person["name"], filename)
        self._index[normalize_name(person["name"])] = filename
        return EMIT_CREATED

    def _find_file_for_person(
        self, person_id: str, matched_name: Optional[str]
    ) -> Optional[str]:
        """Resolve the people filename for a resolved PersonID.

        Prefers the index (matched_name -> filename); falls back to scanning for
        a filename carrying the id's 12-char prefix (the ``_name_to_filename``
        convention) or reading files.
        """
        if matched_name:
            fname = self._index.get(normalize_name(matched_name))
            if fname and (self.people_dir / fname).exists():
                return fname
        # Fallback: filename convention <name>_<id[:12]>.json
        prefix = person_id[:12]
        for f in self.people_dir.glob(f"*_{prefix}.json"):
            return f.name
        return None


def emit_crosswalk_to_people(
    result: CrosswalkResult,
    people_dir: Path,
) -> EmitResult:
    """Converge a crosswalk result into ``output/people/`` (convenience wrapper)."""
    return PeopleEmitter(people_dir).emit(result)
