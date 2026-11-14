"""Equipment under the xAI Batch API: each STAGE of a chapter (broad, refine, identity +
source re-check) is ONE batch round - every request of the stage is queued before the round ends
- and nothing is written until the requests a record depends on are answered. Before: refine
queued one item per round, and identity / source re-check swallowed the batch signal, so records
were written unresolved and without their re-checked fields (2026-11-12)."""

import hashlib
import json
from pathlib import Path

import pytest

from src.extraction import equipment as eq
from src.extraction import equipment_disambiguation as dis
from src.extraction import sub_events as fan
from src.extraction.source_recheck import FieldRecheckSpec, SourceRechecker
from src.grok_client import BatchModeCollecting

TEXT = "The Zorbatron gun fired. Two Quexite carriers landed."
ANSWERS = {
    "equipment_broad": [
        {
            "name": "Zorbatron gun",
            "original_text": "The Zorbatron gun fired.",
            "paragraph_numbers": [1],
        },
        {
            "name": "Quexite carrier",
            "original_text": "Two Quexite carriers landed.",
            "paragraph_numbers": [1],
        },
    ],
    "equipment_disambiguation": {"canonical_name": "Zorbatron Mk I", "confidence": 0.9},
    "equipment_source_recheck": {"country_of_origin": "USA", "quantity": None},
}


def _refined(prompt):
    name = (
        "Zorbatron gun"
        if "Item name (as written): Zorbatron gun" in prompt
        else "Quexite carrier"
    )
    return {
        "Equipment": [
            {
                "common_name": name,
                "category": "artillery",
                "presence": "present",
                "assertion_source": "narrative",
            }
        ]
    }


class BatchClient:
    """Batch-mode stand-in: a request not yet answered is queued and raises BatchModeCollecting;
    ``submit()`` answers every queued request (as a completed batch does)."""

    def __init__(self):
        self.batch_mode, self.answered, self.queued, self.rounds = True, {}, {}, []

    def _get_model(self, _kind):
        return "m"

    def extract_json(self, prompt, cache_type="default", cache_key=None, **_kw):
        key = cache_key or hashlib.sha256(f"{cache_type}|{prompt}".encode()).hexdigest()
        if key in self.answered:
            return json.loads(json.dumps(self.answered[key]))
        if not self.batch_mode:
            raise AssertionError(f"live call in the final pass: {cache_type}")
        self.queued[key] = (cache_type, prompt)
        raise BatchModeCollecting(key)

    def submit(self):
        kinds = sorted({ct for ct, _ in self.queued.values()})
        self.rounds.append((kinds, len(self.queued)))
        for key, (ct, prompt) in self.queued.items():
            self.answered[key] = _answer(ct, prompt)
        self.queued = {}


def _answer(ct, prompt):
    if ct == "equipment_refine":
        return _refined(prompt)
    if ct == "equipment_disambiguation" and "Zorbatron" not in prompt:
        return {"canonical_name": "Quexite Mk II", "confidence": 0.9}
    return ANSWERS.get(ct, {})


@pytest.fixture
def isolated_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(dis, "_LEARNED_PATH", tmp_path / "learned.yaml")
    monkeypatch.setattr(dis, "_SUGGESTIONS_PATH", tmp_path / "suggestions.jsonl")
    monkeypatch.setattr(dis, "_CACHE", {})
    monkeypatch.setattr(dis, "_LEARNED_CACHE", None)


@pytest.fixture
def chapter(tmp_path, monkeypatch, isolated_identity):
    # no encyclopedia text: enrichment makes no Grok call
    monkeypatch.setattr(eq, "_apply_grokipedia_text", lambda *a, **k: None)
    monkeypatch.setattr(eq, "_apply_wikipedia_text_extract", lambda *a, **k: None)
    ev = tmp_path / "chapter1a-event.json"
    ev.write_text(
        json.dumps(
            {
                "Event": {
                    "EventID": "01HZZZZZZZZZZZZZZZZZZZZZE0",
                    "Event_Name": "Landing",
                    "Sub-events": [
                        {
                            "Sub-eventID": "01HZZZZZZZZZZZZZZZZZZZZZS1",
                            "Sub-event_fulltext": {"Paragraph_1": TEXT},
                        }
                    ],
                }
            }
        )
    )
    return ev, tmp_path


def _run(ev, root, client):
    return eq.extract_equipment_from_event(
        event_file=ev,
        output_dir=root / "equipment",
        grok_client=client,
        output_root=root,
    )


def _records(root):
    return [
        p
        for p in (root / "equipment").glob("*.json")
        if p.name not in ("index.json", ".processed_events.json")
    ]


def test_each_stage_is_one_round_and_nothing_is_written_early(chapter):
    ev, root = chapter
    client = BatchClient()
    for _ in range(6):
        try:
            files = _run(ev, root, client)
            break
        except BatchModeCollecting:
            assert _records(root) == []  # no record before its requests are answered
            client.submit()
    else:
        pytest.fail("still collecting after 6 rounds")
    stages = [kinds for kinds, _n in client.rounds]
    # identity first, then the re-check: it asks only for fields still missing, and identity
    # resolution can fill country_of_origin
    assert stages == [
        ["equipment_broad"],
        ["equipment_refine"],
        ["equipment_disambiguation"],
        ["equipment_source_recheck"],
    ]
    counts = [n for _k, n in client.rounds]
    assert counts[1] == 2 and counts[3] == 2  # every item of a stage, in one round
    assert len(files) == 2
    zorbatron = [
        json.loads(p.read_text()) for p in _records(root) if "Zorbatron" in p.name
    ][0]
    assert zorbatron["canonical_name"] == "Zorbatron Mk I"  # resolved, not "raw"
    assert (
        zorbatron["country_of_origin"] == "USA"
    )  # the re-check was applied, not skipped


def test_identity_resolution_does_not_cache_raw_while_collecting(isolated_identity):
    client = BatchClient()
    with pytest.raises(BatchModeCollecting):
        dis.resolve_designation("Zorbatron gun", client)
    assert "zorbatron gun" not in dis._CACHE


def test_the_source_recheck_propagates_collecting():
    spec = FieldRecheckSpec(
        fields={"country_of_origin": "ISO alpha-3"},
        needed=lambda rec: ["country_of_origin"],
        label="equipment",
        name_fields=("common_name",),
    )
    record = {"common_name": "X", "event_mentions": [{"original_text": "X fired."}]}
    with pytest.raises(BatchModeCollecting):
        SourceRechecker(spec).recheck(record, BatchClient())


def test_the_injected_mention_is_removed_when_the_recheck_collects():
    data = {"common_name": "X", "category": "artillery"}
    with pytest.raises(BatchModeCollecting):
        eq._recheck_equipment_fields(data, {"original_text": "X fired."}, BatchClient())
    assert "event_mentions" not in data


def test_run_all_queues_every_item_then_raises_once_and_keeps_order():
    seen = []

    def one(i):
        seen.append(i)
        if i % 2:
            raise BatchModeCollecting(str(i))
        return i * 10

    with pytest.raises(BatchModeCollecting):
        fan.run_all(one, [1, 2, 3, 4], "x", {}, workers=1)
    assert seen == [1, 2, 3, 4]
    assert fan.run_all(lambda i: i * 10, [3, 1, 2], "x", {}) == [
        (3, 30),
        (1, 10),
        (2, 20),
    ]


def _rounds(ev, root, client, after_round=None, limit=8):
    for n in range(limit):
        try:
            return _run(ev, root, client)
        except BatchModeCollecting:
            if after_round:
                after_round(n)
            client.submit()
    pytest.fail(f"still collecting after {limit} rounds")


def test_a_persist_stage_request_defers_only_its_item_and_a_rerun_duplicates_nothing(
    chapter, monkeypatch
):
    """Enrichment is asked while records are written: the item waiting for it is written next
    round; the one already written is a no-op then (no second record, no second mention).
    """
    ev, root = chapter
    text = (
        "The Zorbatron gun was a WWII field gun.",
        "https://grokipedia.com/page/Zorbatron",
    )
    monkeypatch.setattr(
        eq,
        "_apply_grokipedia_text",
        lambda data, name: text if "Zorbatron" in name else None,
    )
    ANSWERS["equipment_enrichment"] = {"description": "A WWII field gun."}
    client = BatchClient()
    written = {}

    def snapshot(n):
        written[n] = sorted(p.name.split("_")[0] for p in _records(root))

    try:
        files = _rounds(ev, root, client, after_round=snapshot)
    finally:
        ANSWERS.pop("equipment_enrichment")
    enrich_round = next(
        i
        for i, (kinds, _n) in enumerate(client.rounds)
        if "equipment_enrichment" in kinds
    )
    assert written[enrich_round] == [
        "Quexite"
    ]  # written; Zorbatron waits for its enrichment
    assert len(files) == 2 and len(_records(root)) == 2
    for p in _records(root):
        assert len(json.loads(p.read_text())["event_mentions"]) == 1
    zorbatron = [
        json.loads(p.read_text()) for p in _records(root) if "Zorbatron" in p.name
    ][0]
    assert zorbatron["description"] == "A WWII field gun."


def test_a_failed_item_leaves_the_chapter_unmarked_for_a_retry(chapter, monkeypatch):
    ev, root = chapter
    client = BatchClient()
    real = eq._prepare_equipment_item

    def flaky(d, *a, **k):
        if "Quexite" in str(d.get("common_name")):
            raise ValueError("broken item")
        return real(d, *a, **k)

    monkeypatch.setattr(eq, "_prepare_equipment_item", flaky)
    files = _rounds(ev, root, client)
    assert len(files) == 1
    registry = root / "equipment" / ".processed_events.json"
    assert not registry.exists() or str(ev) not in json.loads(registry.read_text())


def test_a_refine_error_keeps_the_broad_find(monkeypatch):
    def boom(*a, **k):
        raise KeyError("shape")

    monkeypatch.setattr(eq, "_refine_equipment_item", boom)
    item = {
        "name": "LCT",
        "original_text": "The LCTs beached.",
        "paragraph_numbers": [3],
    }
    rec = eq._refine_or_fallback(item, {}, None)
    assert rec["common_name"] == "LCT" and rec["_refine_fallback"]
    assert rec["original_text"] == "The LCTs beached." and rec["paragraph_numbers"] == [
        3
    ]


def test_an_identity_mismatch_is_raised_not_counted():
    from src.grok_client import GrokIdentityError

    def one(i):
        if i == 2:
            raise GrokIdentityError("another request's answer")
        return i

    with pytest.raises(GrokIdentityError):
        fan.run_all(one, [1, 2, 3], "x", {})


def test_the_enrichment_key_does_not_depend_on_the_fetched_text():
    client = BatchClient()
    urls = ["https://grokipedia.com/page/X"]
    a = eq._enrichment_cache_key(client, "X", "X", "artillery", urls)
    b = eq._enrichment_cache_key(client, "X", "X", "artillery", list(urls))
    assert a == b and a != eq._enrichment_cache_key(client, "Y", "Y", "artillery", urls)
    assert eq._enrichment_cache_key(client, "X", "X", "artillery", None) is None
