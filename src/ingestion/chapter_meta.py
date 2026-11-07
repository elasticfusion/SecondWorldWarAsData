"""ONE builder for the ``chapter1-meta.yaml`` written by every Phase-0 converter (DRY).

Used by phase0_convert (EPUB/DOCX/HTML), phase0_video (transcripts) and the OCR merge handler.
SAFE YAML via ``yaml.safe_dump`` (never an f-string) so a book/key with YAML metacharacters cannot
inject structure (P2-C1). author/series/license stay '' (never a 'TODO - Add ...' that masquerades
as data); the gap is recorded by the caller's needs-review/metadata marker.

``source_url`` must be a real URL (http(s) or s3://) or "" — never a descriptive placeholder: the
parser resolves relative images/maps against it. ``source_url_basis`` says where it came from and
``source_key`` keeps provenance back to the uploaded object.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import yaml


def build_meta_yaml(
    book: str,
    *,
    source_url: str,
    source_url_basis: str,
    source_key: str,
    chapter_title: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Serialize the chapter meta (see module docstring). ``extra`` adds converter-specific keys."""
    meta: Dict[str, Any] = {
        "series": "",
        "book": book,
        "author": "",
        "chapter_number": "1",
        "chapter_title": chapter_title,
        "license": "",
        "copyright_date": "",
        "source_url": source_url,
        "source_url_basis": source_url_basis,
        "source_key": source_key,
    }
    meta.update(extra or {})
    return yaml.safe_dump(meta, default_flow_style=False, allow_unicode=True).encode(
        "utf-8"
    )
