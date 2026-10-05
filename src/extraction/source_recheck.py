"""Reusable source-recheck gap-fill — for ANY entity type (people, people_groups,
places, equipment, events, …).

Principle (owner-confirmed, generalized): when an entity is missing a REQUIRED/critical
field, recover it from the **retained source text** (``event_mentions[].original_text``,
``original_text``, or a provided extractor — no re-fetch of the PDF) rather than guessing
externally. Source-FIRST; gap-fill ONLY (never overwrite a stated value); provenance
recorded (``_provenance[field] = {sourced_from, confidence}``); fail-safe.

Usage — declare a spec per entity type and reuse the engine:

    spec = FieldRecheckSpec(
        fields={
            "nationality": 'ISO 3166-1 alpha-3 (e.g. USA, DEU) or null',
            "parent_organization": 'the parent unit name or null',
        },
        needed=lambda rec: [f for f in (...) if not rec.get(f)],  # which are missing
    )
    SourceRechecker(spec).recheck(record, grok_client)

The group-specific logic (CC parent division, source-book nationality hint) lives in the
caller's ``needed``/post-hooks, keeping this engine entity-agnostic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def default_source_text(record: Dict[str, Any], limit: int = 6000) -> str:
    """Default extractor: concatenate retained source passages from event_mentions'
    original_text (falling back to a top-level original_text)."""
    chunks: List[str] = []
    for m in record.get("event_mentions", []) or []:
        t = m.get("original_text") or ""
        if t:
            chunks.append(t)
        if sum(len(c) for c in chunks) > limit:
            break
    if not chunks and record.get("original_text"):
        chunks.append(record["original_text"])
    return " ".join(chunks)[:limit]


@dataclass
class FieldRecheckSpec:
    """Describes, for one entity type, which fields can be source-rechecked and how."""

    #: field name -> human instruction for the extraction prompt
    fields: Dict[str, str]
    #: record -> list of field names currently MISSING (and thus to recheck)
    needed: Callable[[Dict[str, Any]], List[str]]
    #: how to pull the entity's retained source text (default: event_mentions)
    source_text: Callable[[Dict[str, Any]], str] = default_source_text
    #: entity label used in the prompt + cache_type (e.g. "people_group")
    label: str = "entity"
    #: name field candidates to describe the entity in the prompt
    name_fields: tuple = ("name", "group_name", "current_name")
    #: optional post-hook(record, filled_fields)->extra_filled for fallbacks (e.g. a
    #: source-book nationality hint). Runs after the LLM result is applied.
    post_hook: Optional[Callable[[Dict[str, Any], List[str]], int]] = None
    #: optional per-field type coercion applied to the stringified LLM value before it
    #: is stored (e.g. {"quantity": int}). A coercion that raises skips that field.
    coerce: Dict[str, Callable[[str], Any]] = field(default_factory=dict)
    confidence: float = 0.8


class SourceRechecker:
    """Entity-agnostic engine that fills missing required fields from retained source."""

    def __init__(self, spec: FieldRecheckSpec):
        self.spec = spec

    def _entity_name(self, record: Dict[str, Any]) -> str:
        for f in self.spec.name_fields:
            if record.get(f):
                return record[f]
        return ""

    def recheck(self, record: Dict[str, Any], grok_client: Any) -> int:
        """Gap-fill the record's missing required fields from its retained source.
        Returns the count filled. Fail-safe."""
        needed = self.spec.needed(record)
        if not needed:
            return 0
        name = self._entity_name(record)
        source = self.spec.source_text(record)
        res = None
        if name and source:
            try:
                res = grok_client.extract_json(
                    prompt=self._build_prompt(name, source, needed),
                    use_cache=True,
                    cache_type=f"{self.spec.label}_source_recheck",
                )
            except Exception as e:  # noqa: BLE001 - fail-safe
                logger.warning("source-recheck skipped for %s: %s", name, e)
        return self._apply(record, res, needed)

    def _build_prompt(self, name: str, source: str, needed: List[str]) -> str:
        asks = ", ".join(
            f'"{f}": {self.spec.fields[f]}' for f in needed if f in self.spec.fields
        )
        return (
            f"From this WWII source passage, about the {self.spec.label} '{name}', "
            f"extract ONLY what the text states (use null if not stated — do NOT "
            f"guess):\n{{{asks}}}\n\nPassage:\n{source}\n\nReturn ONLY valid JSON."
        )

    def _apply(self, record: Dict[str, Any], res, needed: List[str]) -> int:
        filled: List[str] = []
        if isinstance(res, dict):
            for f in needed:
                val = res.get(f)
                sval = str(val).strip() if val is not None else ""
                if sval and sval.lower() != "null" and not record.get(f):
                    stored: Any = sval
                    if f in self.spec.coerce:
                        try:
                            stored = self.spec.coerce[f](sval)
                        except (ValueError, TypeError):
                            continue  # unparseable for this field -> skip, don't store
                    record[f] = stored
                    record.setdefault("_provenance", {})[f] = {
                        "sourced_from": "original_text",
                        "confidence": self.spec.confidence,
                    }
                    filled.append(f)
        extra = 0
        if self.spec.post_hook:
            try:
                extra = self.spec.post_hook(record, filled) or 0
            except Exception as e:  # noqa: BLE001
                logger.debug("recheck post_hook skipped: %s", e)
        return len(filled) + extra


def set_sourced(
    record: Dict[str, Any], field_name: str, value, sourced_from: str, confidence: float
) -> None:
    """Helper for post-hooks: set a field + record its provenance."""
    record[field_name] = value
    record.setdefault("_provenance", {})[field_name] = {
        "sourced_from": sourced_from,
        "confidence": confidence,
    }
