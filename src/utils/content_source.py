"""Content-source path scheme for ``contentrepository/`` uploads.

Primary-source files carry a SOURCE identifier in their path so the platform can tell WHO
provided a file — the system/operator vs. (future) individual contributors who upload their
own primary-source records.

Path scheme (forward)::

    contentrepository/{SOURCE_ID}/{TITLE}/...

- ``SOURCE_ID`` identifies the provider. The reserved identifier for platform/operator-
  ingested material is ``_system`` (leading underscore = reserved platform namespace; ``_``
  is a safe path/DynamoDB/arg token). Contributor uploads use e.g. ``user-<id>``.
- BACKWARD COMPATIBILITY: the existing corpus uses ``contentrepository/{TITLE}/...`` with NO
  source segment. Such legacy/unprefixed paths are treated as system-provided — their source
  is ``_system`` and the first segment is the TITLE.

The identifier flows into filesystem paths, DynamoDB keys, and a subprocess ``--book`` arg, so
every segment must be a safe token (``[A-Za-z0-9._-]+``); unsafe tokens are rejected upstream.
"""

import re
from typing import Tuple

# Reserved source identifier for system/operator-ingested primary sources. A contributor
# source id must NOT collide with this reserved namespace (leading underscore is reserved).
SYSTEM_SOURCE_ID = "_system"

_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._-]+")

# A path segment is recognized as a SOURCE id (vs. a title) when it is the reserved system
# id or a contributor namespace (user-/contrib-/org-). Anything else in the first position
# is treated as a legacy TITLE (source defaults to _system) for backward compatibility.
_SOURCE_PREFIXES = ("user-", "contrib-", "org-")


def is_source_segment(segment: str) -> bool:
    """True if a path segment denotes a SOURCE identifier (reserved system id or a
    contributor namespace), as opposed to a legacy title segment."""
    if segment == SYSTEM_SOURCE_ID:
        return True
    return segment.lower().startswith(_SOURCE_PREFIXES)


def parse_content_key(key: str) -> Tuple[str, str]:
    """Return ``(source_id, title)`` for a ``contentrepository/`` key.

    - ``contentrepository/{SOURCE_ID}/{TITLE}/...`` -> that source + title.
    - Legacy ``contentrepository/{TITLE}/...`` (no source segment) -> (``_system``, title).
    - Loose/odd shapes -> (``_system``, filename stem) so the item still gets a title.
    The title is NOT re-validated here (the caller applies its safe-title rule); source
    defaults to ``_system`` whenever absent.
    """
    parts = [p for p in (key or "").split("/") if p]
    if not parts or parts[0] != "contentrepository":
        # not a content key -> system + filename stem fallback
        stem = parts[-1].rsplit(".", 1)[0] if parts else ""
        return SYSTEM_SOURCE_ID, stem
    rest = parts[1:]
    if rest and is_source_segment(rest[0]):
        return _parse_with_source_segment(rest)
    return _parse_legacy_content_key(rest)


def _parse_with_source_segment(rest):
    """``contentrepository/{SOURCE_ID}/{TITLE}/...``: explicit source segment present."""
    source_id = rest[0]
    title = (
        rest[1] if len(rest) >= 2 else (rest[-1].rsplit(".", 1)[0] if rest[1:] else "")
    )
    return source_id, title


def _parse_legacy_content_key(rest):
    """Legacy ``contentrepository/{TITLE}/...`` (no source segment) -> (_system, title)."""
    if len(rest) >= 2:
        return SYSTEM_SOURCE_ID, rest[0]  # {TITLE}/...
    stem = rest[-1].rsplit(".", 1)[0] if rest else ""
    return SYSTEM_SOURCE_ID, stem
