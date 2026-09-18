"""Routing manifest: the ingestion front-end's hand-off artifact.

A :class:`RoutingManifest` combines the recorded-original metadata (steps 1-2)
with the per-page disposition results (step 3) into one serializable document
that stage 3 consumes to convert each region with a disposition-appropriate
method.

Contiguous pages that share a disposition are coalesced into a single
:class:`RoutingRegion` with a page range, so stage 3 can request "convert pages
380-410 as structured" in one call. Regions that need human review are
preserved and summarized.

See docs/current/dataquality/INGESTION_FRONT_END.md (step 4).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.ingestion.disposition import DecisionSource, Disposition, DispositionResult
from src.ingestion.source_metadata import SourceMetadata


@dataclass
class RoutingRegion:  # pylint: disable=too-many-instance-attributes
    """A contiguous run of pages/section that share one disposition.

    Attributes:
        disposition: The disposition for this region.
        start_page: 1-based first page (inclusive); None for non-paged sources.
        end_page: 1-based last page (inclusive); None for non-paged sources.
        section_id: Optional section identifier for non-paged sources.
        needs_review: True if any page in the region was flagged for review.
        min_confidence: Lowest per-page confidence in the region.
        decision_source: "config_override" if any page in the region was
            overridden, else "heuristic".
        provenance_anchor: Citation anchor for the region start.
    """

    disposition: Disposition
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    section_id: Optional[str] = None
    needs_review: bool = False
    min_confidence: float = 1.0
    decision_source: DecisionSource = "heuristic"
    provenance_anchor: str = ""

    @property
    def page_count(self) -> int:
        """Number of pages spanned (0 for non-paged regions)."""
        if self.start_page is None or self.end_page is None:
            return 0
        return self.end_page - self.start_page + 1


@dataclass
class ManifestSummary:
    """Aggregate counts describing a manifest at a glance."""

    total_pages: int = 0
    total_regions: int = 0
    regions_needing_review: int = 0
    pages_by_disposition: Dict[str, int] = field(default_factory=dict)


@dataclass
class RoutingManifest:
    """Front-end output: recorded original + routed regions + summary."""

    source: SourceMetadata
    regions: List[RoutingRegion] = field(default_factory=list)
    summary: ManifestSummary = field(default_factory=ManifestSummary)

    def regions_for(self, disposition: Disposition) -> List[RoutingRegion]:
        """Return regions matching ``disposition`` in manifest order."""
        return [r for r in self.regions if r.disposition == disposition]

    def regions_needing_review(self) -> List[RoutingRegion]:
        """Return regions flagged for human review."""
        return [r for r in self.regions if r.needs_review]

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "source": self.source.to_dict(),
            "regions": [asdict(r) for r in self.regions],
            "summary": asdict(self.summary),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RoutingManifest":
        """Rebuild a manifest from a dict produced by :meth:`to_dict`."""
        summary_data = data.get("summary", {})
        return cls(
            source=SourceMetadata.from_dict(data["source"]),
            regions=[RoutingRegion(**r) for r in data.get("regions", [])],
            summary=(
                ManifestSummary(**summary_data) if summary_data else ManifestSummary()
            ),
        )

    def save(self, path: Path) -> None:
        """Write the manifest to ``path`` as pretty JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "RoutingManifest":
        """Read a manifest previously written by :meth:`save`."""
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _extends_region(
    region: Optional[RoutingRegion],
    prev_page: Optional[int],
    result: DispositionResult,
) -> bool:
    """True if ``result`` continues ``region`` (same disposition, next page)."""
    return (
        region is not None
        and region.disposition == result.disposition
        and prev_page is not None
        and result.page_number is not None
        and result.page_number == prev_page + 1
    )


def _coalesce_regions(results: Sequence[DispositionResult]) -> List[RoutingRegion]:
    """Group consecutive results that share a disposition into regions.

    Consecutive pages are merged only when they are adjacent (page N followed by
    N+1) and share a disposition, so a gap or a disposition change starts a new
    region.
    """
    regions: List[RoutingRegion] = []
    current: Optional[RoutingRegion] = None
    prev_page: Optional[int] = None

    for result in results:
        if _extends_region(current, prev_page, result) and current is not None:
            current.end_page = result.page_number
            current.needs_review = current.needs_review or result.needs_review
            current.min_confidence = min(current.min_confidence, result.confidence)
            if result.decision_source == "config_override":
                current.decision_source = "config_override"
        else:
            current = RoutingRegion(
                disposition=result.disposition,
                start_page=result.page_number,
                end_page=result.page_number,
                section_id=result.section_id,
                needs_review=result.needs_review,
                min_confidence=result.confidence,
                decision_source=result.decision_source,
                provenance_anchor=result.provenance_anchor,
            )
            regions.append(current)
        prev_page = result.page_number

    return regions


def _summarize(
    results: Sequence[DispositionResult], regions: Sequence[RoutingRegion]
) -> ManifestSummary:
    """Build the aggregate summary from results and coalesced regions."""
    by_disposition: Dict[str, int] = {}
    for result in results:
        by_disposition[result.disposition] = (
            by_disposition.get(result.disposition, 0) + 1
        )
    return ManifestSummary(
        total_pages=len(results),
        total_regions=len(regions),
        regions_needing_review=sum(1 for r in regions if r.needs_review),
        pages_by_disposition=by_disposition,
    )


def build_manifest(
    source: SourceMetadata,
    results: Sequence[DispositionResult],
) -> RoutingManifest:
    """Assemble a routing manifest from source metadata and page results.

    For an unsupported source (``source.supported`` is False) an empty-region
    manifest is returned, so callers get a consistent artifact recording that
    the source was seen but not routed. Any ``results`` passed are still
    coalesced and summarized.
    """
    regions = _coalesce_regions(results)
    summary = _summarize(results, regions)
    return RoutingManifest(source=source, regions=regions, summary=summary)
