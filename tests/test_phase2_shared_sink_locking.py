"""PR4: shared human-review / index sinks must use a LOCKED read-modify-write so concurrent
workers don't lose-update (silent drop). Threaded appenders -> every item must survive.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from src.extraction.recall_gap import _append_recall_gap_items


def test_review_queue_append_no_lost_update_under_concurrency(tmp_path: Path):
    people = tmp_path / "people"
    people.mkdir()
    n = 20

    def worker(i: int):
        _append_recall_gap_items(people, [{"name": f"item{i}"}])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    queue = json.loads((people / "review_queue.json").read_text())
    names = {x["name"] for x in queue}
    assert len(queue) == n, f"lost update: expected {n}, got {len(queue)}"
    assert names == {f"item{i}" for i in range(n)}


def test_place_image_append_no_lost_update(tmp_path: Path):
    from src.extraction.image_place_link import _append_place_image

    places = tmp_path / "places"
    places.mkdir()
    pf = places / "Caen_01HTEST0000000000000000PL.json"
    pf.write_text(
        json.dumps({"PlaceID": "01HTEST0000000000000000PL", "current_name": "Caen"}),
        encoding="utf-8",
    )

    def worker(i: int):
        _append_place_image(pf, f"https://x/img{i}.jpg", "cap", "Book", None)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rec = json.loads(pf.read_text())
    urls = {e["url"] for e in rec.get("images", [])}
    assert (
        len(rec.get("images", [])) == 10
    ), f"lost image append: {len(rec.get('images', []))}"
    assert urls == {f"https://x/img{i}.jpg" for i in range(10)}
