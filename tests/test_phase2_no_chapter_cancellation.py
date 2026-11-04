"""Guard: Phase-2 chapter/entity work is never cancelled by a wall-clock timeout.

XChannel ch8: synchronous in-loop people enrichment blocked the event loop ~2.8h; when it returned
the hard 300s/180s ``asyncio.wait_for`` deadlines were long past and cancelled the chapter BEFORE
the entity-ULID write-back into the event file -> links lost, re-extraction, 31 dangling mentions.

The old deadlines can't be hit cheaply in a test, so ``asyncio.wait_for`` is patched to a tiny
timeout: on the old code that cancels the slow entity and the write-back loses its links; the fixed
code never calls it.
"""

import asyncio
import json
import logging
import time

import pytest

EID = "01M6J3NGH7SB27Q1Q9QAKG9H7N"
SID = "01M6J3NGB3DR9QD7VT6ST9Y8SH"
PID = "01M6J3NGJ6MPN7Q9HDRMAZP4Z8"
QID = "01M6J3NGC28PSHFMM8WEFQEKC3"

from src.extraction import batch_parallel as bp


def _files(tmp_path):
    ev = tmp_path / "ch-event.json"
    ev.write_text(
        json.dumps(
            {
                "Event": {
                    "EventID": EID,
                    "Event_Name": "x",
                    "Sub-events": [{"Sub-eventID": SID}],
                }
            }
        ),
        encoding="utf-8",
    )
    pf = tmp_path / "ch-parsed.json"
    pf.write_text(json.dumps({"book": "B", "paragraphs": []}), encoding="utf-8")
    return ev, pf


@pytest.fixture
def _extractors(monkeypatch):
    async def quick(*a, **k):
        return {"count": 1, "links": {SID: [QID]}}

    async def slow_blocking(*a, **k):
        await asyncio.sleep(0.01)  # the API call (run_in_executor) yields first ...
        time.sleep(0.3)  # ... then synchronous in-loop enrichment blocks the loop
        return {"count": 1, "links": {SID: [PID]}}

    monkeypatch.setattr(bp, "extract_dates_batch_async", quick)
    monkeypatch.setattr(bp, "extract_places_batch_async", quick)
    monkeypatch.setattr(bp, "extract_people_groups_batch_async", quick)
    monkeypatch.setattr(bp, "extract_people_batch_async", slow_blocking)
    real = asyncio.wait_for
    monkeypatch.setattr(
        bp.asyncio, "wait_for", lambda aw, timeout=None: real(aw, timeout=0.05)
    )


def test_slow_entity_still_written_back(tmp_path, _extractors):
    ev, pf = _files(tmp_path)
    asyncio.run(bp.extract_all_async(ev, pf, None, tmp_path, {}))
    se = json.loads(ev.read_text())["Event"]["Sub-events"][0]
    assert se.get("people") == [PID]


def test_slow_work_is_surfaced_not_killed(monkeypatch):
    seen = []
    monkeypatch.setattr(bp, "observe_warn", lambda *a, **k: seen.append((a, k)))

    async def slow():
        time.sleep(0.05)
        return "done"

    assert asyncio.run(bp._observe_slow("ch:people", slow(), 0.01)) == "done"
    assert (
        seen and seen[0][0] == ("phase2", "slow") and seen[0][1]["task"] == "ch:people"
    )


def test_fast_work_is_quiet(monkeypatch):
    seen = []
    monkeypatch.setattr(bp, "observe_warn", lambda *a, **k: seen.append(a))

    async def fast():
        return 1

    asyncio.run(bp._observe_slow("x", fast(), 60))
    assert seen == []


def test_chapter_loop_does_not_cancel_before_write_back(tmp_path, _extractors):
    """Real chain: process_chapters_parallel -> process_chapter_async -> extract_all_async.
    On the old code the CHAPTER-level deadline fires (several awaits out) while the chapter
    still awaits its gather -> TimeoutError, chapter failed, links never written back.
    """
    book = tmp_path / "B"
    book.mkdir()
    ev, pf = _files(book)
    res = asyncio.run(
        bp.process_chapters_parallel(
            parsed_files=[pf],
            grok_client=None,
            output_root=tmp_path,
            config={},
            max_parallel=1,
        )
    )
    assert res["failed"] == 0 and res["processed"] == 1
    se = json.loads(ev.read_text())["Event"]["Sub-events"][0]
    assert se.get("people") == [PID]
