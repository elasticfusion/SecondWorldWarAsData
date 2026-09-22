"""Tests for the flattened-table -> PP-StructureV3 auto-routing bridge.

Covers the routing decision (fires only on flattened task-org tables), graceful
degradation when PaddleOCR is unavailable, and the recovery path via an injected
fake runner (so the test needs neither GPU nor the paddle dependency).
"""

from pathlib import Path

from src.ingestion import table_recovery
from src.ingestion.paddle_structure import RecoveredTable, _extract_tables
from src.ingestion.table_recovery import (
    PageTableRecovery,
    page_needs_recovery,
    recover_page_tables,
)

# A flattened task-org block in the shape Chandra emits for p155-style tables:
# a time-snapshot header, then repeated group headers + short unit tokens.
FLATTENED_MD = """\
TROOP ASSIGNMENTS

170300 - March South

CC-A
40
48
A/33

CC-B
31
23
B/33

DIV ARTY
440
434
489
"""

# Ordinary prose that must NOT trigger recovery.
PROSE_MD = """\
The 7th Armored Division moved south during the night, its combat commands
strung out along the icy roads toward St. Vith. Enemy pressure was mounting.
"""


class _FakeRunner:
    """Stand-in for PaddleStructureRunner that returns a fixed recovered table."""

    def __init__(self, html: str) -> None:
        self._html = html
        self.calls = 0

    def recover(self, image_path: Path) -> RecoveredTable:  # noqa: D401
        self.calls += 1
        return RecoveredTable(
            html=self._html,
            markdown=self._html,
            table_count=len(_extract_tables(self._html)),
            device="cpu",
            notes="fake",
        )


def test_detector_fires_on_flattened_table() -> None:
    spans = page_needs_recovery(FLATTENED_MD)
    assert spans, "expected a flattened task-org table to be detected"


def test_detector_silent_on_prose() -> None:
    assert page_needs_recovery(PROSE_MD) == []


def test_prose_page_is_not_routed() -> None:
    result = recover_page_tables(PROSE_MD)
    assert isinstance(result, PageTableRecovery)
    assert result.routed is False
    assert result.recovered is None
    assert "no flattened table" in result.notes


def test_flattened_page_degrades_when_paddle_unavailable(monkeypatch) -> None:
    # Force the "PaddleOCR not installed" branch regardless of environment.
    monkeypatch.setattr(table_recovery, "is_available", lambda: False)
    result = recover_page_tables(FLATTENED_MD, page_image=Path("/nonexistent.png"))
    assert result.routed is False
    assert result.flattened_spans, "detector result should still be reported"
    assert "unavailable" in result.notes


def test_flattened_page_is_recovered_with_injected_runner(
    monkeypatch, tmp_path
) -> None:
    # Pretend paddle is available and inject a fake runner so no real model runs.
    monkeypatch.setattr(table_recovery, "is_available", lambda: True)
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # content irrelevant to the fake runner
    fake = _FakeRunner("<table><tr><td>CC-A</td><td>170300</td></tr></table>")

    result = recover_page_tables(FLATTENED_MD, page_image=img, runner=fake)  # type: ignore[arg-type]

    assert result.routed is True
    assert fake.calls == 1
    assert result.recovered_a_table is True
    assert result.recovered is not None
    assert "<table>" in result.recovered.html
    # The bridge's own note reports the routing outcome (the runner's internal
    # note is separate and controlled by the fake).
    assert "routed to PP-StructureV3" in result.notes
    assert "1 table(s) recovered" in result.notes


def test_flattened_page_without_image_is_detected_not_recovered(monkeypatch) -> None:
    monkeypatch.setattr(table_recovery, "is_available", lambda: True)
    result = recover_page_tables(FLATTENED_MD)  # no image, no pdf
    assert result.routed is False
    assert result.flattened_spans
    assert "no page image" in result.notes
