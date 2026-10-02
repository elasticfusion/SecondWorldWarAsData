"""Offline/bulk-dataset award-citation source (zero live scraping).

Queries a locally-held dataset of US award recipients + verbatim citations by
name. This is the maximally-polite path: when a bulk dataset of citations is
available on disk (e.g. a downloaded Hall-of-Valor export, the American War
Library DSC list, or a curated CSV/JSON), we read it locally and attach the
citation with full provenance — no network, no WAF, no rate-limit concerns.

Dataset format (JSON): a list of records, each at least::

    {"name": "John A. Doe", "award": "Distinguished Service Cross",
     "citation": "For extraordinary heroism ...",
     "source_name": "American War Library", "source_url": "https://..."}

``award``/``source_name``/``source_url`` are optional. Name matching is
conservative (normalized exact, then last-name + first-initial) to avoid
attaching the wrong person's citation.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List

from src.enrichment.award_sources import AwardCitation, today_iso

logger = logging.getLogger(__name__)


def _norm(name: str) -> str:
    """Normalize a name for matching: lowercase, strip punctuation/extra space."""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _name_key(name: str) -> str:
    """last-name + first-initial key for conservative fallback matching."""
    parts = _norm(name).split()
    if not parts:
        return ""
    return f"{parts[-1]}|{parts[0][:1]}" if len(parts) > 1 else parts[-1]


class OfflineAwardDataset:
    """An :class:`AwardCitationSource` backed by a local JSON dataset."""

    def __init__(self, dataset_path: Path, source_name: str = ""):
        self.name = source_name or f"offline:{dataset_path.name}"
        self._by_exact: Dict[str, List[dict]] = {}
        self._by_key: Dict[str, List[dict]] = {}
        self._load(dataset_path)

    def _load(self, path: Path) -> None:
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - missing/invalid dataset = empty source
            logger.warning("Award dataset %s not loaded: %s", path, e)
            return
        if not isinstance(records, list):
            logger.warning("Award dataset %s is not a list; ignoring", path)
            return
        for rec in records:
            if (
                not isinstance(rec, dict)
                or not rec.get("name")
                or not rec.get("citation")
            ):
                continue
            self._by_exact.setdefault(_norm(rec["name"]), []).append(rec)
            self._by_key.setdefault(_name_key(rec["name"]), []).append(rec)
        logger.info("Loaded %d award citation(s) from %s", len(self._by_exact), path)

    def lookup(self, person_name: str, award_hint: str = "") -> List[AwardCitation]:
        recs = self._by_exact.get(_norm(person_name))
        if not recs:  # conservative fallback
            recs = self._by_key.get(_name_key(person_name))
        if not recs:
            return []
        out: List[AwardCitation] = []
        for rec in recs:
            if award_hint and rec.get("award") and rec["award"] != award_hint:
                continue
            out.append(
                AwardCitation(
                    citation_text=rec["citation"],
                    source_name=rec.get("source_name") or self.name,
                    source_url=rec.get("source_url", ""),
                    retrieved_date=rec.get("retrieved_date") or today_iso(),
                    award=rec.get("award", ""),
                    # Dataset is an authoritative curated source → verified.
                    verified=True,
                )
            )
        return out
