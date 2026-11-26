"""Every schema version change, declared once per entity: from -> to, and its kind (owner 2026-11-25, D2).

* ``FORWARD`` - forward-compatible (a new optional field, a widened type): an older record is brought
  forward on the fly, before a merge or update (``schema_contract.bring_forward``), by the step's
  transform when it has one (most need none) and then checked against the current schema.
* ``BREAKING`` - not forward-compatible (a new required field, a removed/renamed field, a tightened
  type): done only by the upgrade release script, never on the fly; until then the affected work is
  held (D2-Q2).

Bump an entity in ``ENTITY_SCHEMA_VERSIONS`` -> add its step here (tests/test_version_steps.py fails
otherwise). History: per-entity versions began at 2.24 for every entity; every step since then was
additive (the compatibility rule in ``src/schemas/__init__``; no upgrader was ever registered), so all
are FORWARD. A record that still fails the current schema after its steps is reported ``invalid`` by
``bring_forward``, whatever its steps were declared as.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple

FORWARD = "forward"
BREAKING = "breaking"
FIRST_VERSION = (
    "2.24"  # the first per-entity version: an older record needs a release upgrade
)

# entity -> the versions it has had, oldest first; each consecutive pair is one FORWARD step unless
# listed in _BREAKING. Taken from the git history of ENTITY_SCHEMA_VERSIONS (2026-11-25).
_HISTORY: Dict[str, List[str]] = {
    "events": ["2.24", "2.25"],
    "dates": ["2.24", "2.25"],
    "places": ["2.24", "2.25", "2.26", "2.27", "2.28", "2.29", "2.30", "2.31"],
    "people": ["2.24", "2.25", "2.26", "2.27", "2.28"],
    "people_groups": ["2.24", "2.25", "2.26", "2.27", "2.28", "2.29", "2.30"],
    "equipment": ["2.24", "2.25", "2.26", "2.27", "2.28", "2.30", "2.31"],
    "weather": ["2.24", "2.25"],
    "logistics": ["2.24", "2.25"],
    "casualties": ["2.24", "2.25", "2.26", "2.27"],
    "maps": ["2.24", "2.25", "2.26", "2.27"],
    "map_features": ["2.24"],
    "bibliography": [
        "2.24",
        "2.25",
        "2.26",
        "2.27",
        "2.28",
        "2.29",
        "2.30",
        "2.31",
        "2.32",
    ],
    "images": ["2.24", "2.25", "2.26", "2.27", "2.28", "2.29"],
    "source_section": ["2.24", "2.25", "2.26"],
}

# (entity, from, to) of the steps that are NOT forward-compatible. None so far.
_BREAKING: frozenset = frozenset()

# (entity, from, to) -> transform for a FORWARD step that must reshape the record (most need none).
# A transform takes a copy of the record and returns it; it never invents content.
TRANSFORMS: Dict[Tuple[str, str, str], Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


def history(entity: str) -> List[str]:
    """The versions ``entity`` has had, oldest first ([] for an unknown entity)."""
    return list(_HISTORY.get(entity, []))


def steps(entity: str) -> List[Tuple[str, str, str]]:
    """``(from, to, kind)`` for each version change of ``entity``, oldest first."""
    versions = _HISTORY.get(entity, [])
    return [
        (a, b, BREAKING if (entity, a, b) in _BREAKING else FORWARD)
        for a, b in zip(versions, versions[1:])
    ]
