"""Merge pointers: a merged-away record id still reaches the record that absorbed it (owner
2026-11-21: a table of pointers only, never record JSON)."""

import json
import logging
from unittest import mock

import pytest

from src.utils import id_aliases as ia

A, B, C, D = (
    "01HX7YZABCDEFGHJKMNPQRSTVA",
    "01HX7YZABCDEFGHJKMNPQRSTVB",
    "01HX7YZABCDEFGHJKMNPQRSTVC",
    "01HX7YZABCDEFGHJKMNPQRSTVD",
)


@pytest.fixture(autouse=True)
def _local(tmp_path, monkeypatch):
    monkeypatch.setattr(ia, "_LOCAL_FILE", tmp_path / "_id_aliases.jsonl")
    monkeypatch.setattr(ia, "_table", lambda: None)
    ia.reset_local()
    yield
    ia.reset_local()


def test_an_unmerged_id_resolves_to_itself():
    assert ia.resolve("bibliography", A) == A


def test_a_merged_id_resolves_through_a_chain():
    assert ia.record("people", B, A, "dedup")
    assert ia.record("people", C, B, "dedup")  # B was itself merged into A later
    assert ia.resolve("people", C) == A
    assert ia.resolve("places", C) == C  # pointers are per entity


def test_a_cycle_in_stored_pointers_is_said_not_followed(caplog):
    """record() refuses a cycle; resolve() still stops on one already in the store."""
    for old, new in ((A, B), (B, A)):
        with open(ia._LOCAL_FILE, "a") as f:
            f.write(
                json.dumps({"alias_key": f"people#{old}", "survivor_id": new}) + "\n"
            )
    ia.reset_local()
    with caplog.at_level(logging.ERROR):
        assert ia.resolve("people", A) is None
    assert "OBSERV id_aliases.cycle" in caplog.text


@pytest.mark.parametrize(
    "entity,old,new",
    [("weather", A, B), ("people", "x", B), ("people", A, A), ("people", A, "../x")],
)
def test_bad_pointers_are_refused(entity, old, new, caplog):
    with caplog.at_level(logging.ERROR):
        assert ia.record(entity, old, new, "t") is False
    assert "OBSERV id_aliases.refused" in caplog.text


def test_pointers_survive_a_new_process():
    ia.record("bibliography", B, A, "bib_reconcile")
    ia.reset_local()
    assert ia.resolve("bibliography", B) == A


def test_dynamodb_item_shape_and_failure(monkeypatch, caplog):
    table = mock.Mock()
    table.get_item.return_value = {}  # no pointer stored yet
    monkeypatch.setattr(ia, "_table", lambda: table)
    assert ia.record("bibliography", B, A, "bib_reconcile")
    item = table.put_item.call_args.kwargs["Item"]
    assert item["alias_key"] == f"bibliography#{B}" and item["survivor_id"] == A
    assert set(item) == {
        "alias_key",
        "entity",
        "old_id",
        "survivor_id",
        "source",
        "merged_at",
    }
    table.put_item.side_effect = RuntimeError("AccessDenied")
    with caplog.at_level(logging.ERROR):
        assert ia.record("bibliography", C, A, "t") is False
    assert "OBSERV id_aliases.write_failed" in caplog.text and "kept" in caplog.text


def test_the_table_name_follows_the_cache_table(monkeypatch):
    monkeypatch.delenv("ID_ALIAS_TABLE", raising=False)
    monkeypatch.setenv("CACHE_TABLE", "prod-wwii-api-cache")
    assert ia._table_name() == "prod-wwii-id-aliases"
    monkeypatch.setenv("ID_ALIAS_TABLE", "x-aliases")
    assert ia._table_name() == "x-aliases"


def test_bibliography_pointers_are_stored_for_every_absorbed_id(tmp_path):
    from src.extraction.bibliography import record_merge_pointers

    (tmp_path / "bib_1.json").write_text(
        json.dumps({"BibliographyID": A, "merged_from": [B, C]})
    )
    (tmp_path / "bib_2.json").write_text(json.dumps({"BibliographyID": D}))
    assert record_merge_pointers(tmp_path) is True
    assert ia.resolve("bibliography", B) == A and ia.resolve("bibliography", C) == A


def test_a_failed_pointer_defers_the_s3_delete(tmp_path, monkeypatch, caplog):
    """ecs_entrypoint deletes merged-away bibliography files only once their pointers are stored."""
    from src.extraction import bibliography as b

    (tmp_path / "bib_1.json").write_text(
        json.dumps({"BibliographyID": A, "merged_from": [B]})
    )
    monkeypatch.setattr(ia, "record", lambda *a: False)
    assert b.record_merge_pointers(tmp_path) is False


def test_a_dedup_merge_keeps_the_secondary_until_its_pointer_is_stored(
    tmp_path, monkeypatch
):
    from src.dedup import merge as M

    monkeypatch.setattr(ia, "record", lambda *a: False)
    assert M.store_merge_pointer("people", B, A) is False
    assert (
        M.store_merge_pointer("people", "", A) is True
    )  # no id: nothing to point from
    assert (
        M.store_merge_pointer("people", "P2", "P1") is True
    )  # not ULIDs: no reference holds them


def test_a_real_dedup_merge_stores_the_pointer_then_deletes(tmp_path):
    """merge_generic: the secondary file goes only after its pointer is stored."""
    from src.dedup import merge as M

    d = tmp_path / "places"
    d.mkdir()
    (d / "a.json").write_text(
        json.dumps({"PlaceID": A, "name": "Metz", "event_mentions": []})
    )
    (d / "b.json").write_text(
        json.dumps({"PlaceID": B, "name": "Metz", "event_mentions": []})
    )
    people = [
        {"filename": "a.json", "name": "Metz"},
        {"filename": "b.json", "name": "Metz"},
    ]
    with (
        mock.patch.object(M, "_write_entity_guarded", return_value=True),
        mock.patch.object(M, "_backup_before_delete", return_value=True),
        mock.patch.object(M, "_rebase_refs_for_merge"),
    ):
        M.merge_generic(d, people, 0, id_field="PlaceID")
    assert not (d / "b.json").exists()
    assert ia.resolve("places", B) == A


def test_a_pointer_is_written_once_and_never_redirected(caplog):
    assert ia.record("people", B, A, "dedup")
    assert ia.record("people", B, A, "dedup")  # a re-run: already stored
    with caplog.at_level(logging.ERROR):
        assert ia.record("people", B, C, "dedup") is False  # would redirect B's holders
    assert ia.resolve("people", B) == A
    assert "OBSERV id_aliases.conflict" in caplog.text


def test_a_pointer_that_would_close_a_cycle_is_refused():
    assert ia.record("people", B, A, "dedup")
    assert ia.record("people", A, B, "dedup") is False
    assert ia.resolve("people", B) == A


def test_a_trailing_newline_is_not_a_ulid():
    assert ia.record("people", B + "\n", A, "t") is False


def test_dynamodb_write_is_conditional(monkeypatch):
    table = mock.Mock()
    table.get_item.return_value = {}
    monkeypatch.setattr(ia, "_table", lambda: table)
    assert ia.record("places", B, A, "dedup")
    assert table.put_item.call_args.kwargs["ConditionExpression"] == (
        "attribute_not_exists(alias_key)"
    )


def test_the_same_record_twice_needs_no_pointer():
    from src.dedup import merge as M

    assert M.store_merge_pointer("people", A, A) is True


def test_an_unreadable_record_defers_the_delete(tmp_path):
    from src.extraction.bibliography import record_merge_pointers

    (tmp_path / "bib_bad.json").write_text("{not json")
    assert record_merge_pointers(tmp_path) is False


def test_merged_from_of_the_wrong_type_is_ignored(tmp_path):
    from src.extraction.bibliography import record_merge_pointers

    (tmp_path / "bib_1.json").write_text(
        json.dumps({"BibliographyID": A, "merged_from": B})
    )
    assert (
        record_merge_pointers(tmp_path) is True and ia.resolve("bibliography", B) == B
    )


def test_a_survivor_that_is_merged_later_keeps_its_earlier_pointers(tmp_path, caplog):
    """S1 absorbed A (A -> S1); N (a lower id) later absorbs S1, so N's merged_from is [S1, A].
    The reconcile must accept A (its chain A -> S1 -> N leads to N) with no error, every run.
    """
    from src.extraction.bibliography import record_merge_pointers

    S1, N = C, A  # N sorts lowest: the new survivor
    old = D
    assert ia.record("bibliography", old, S1, "bib_reconcile")
    (tmp_path / "bib_1.json").write_text(
        json.dumps({"BibliographyID": N, "merged_from": sorted([S1, old])})
    )
    with caplog.at_level(logging.ERROR):
        assert record_merge_pointers(tmp_path) is True
        assert record_merge_pointers(tmp_path) is True  # and again next run
    assert "OBSERV id_aliases.conflict" not in caplog.text
    assert ia.resolve("bibliography", old) == N and ia.resolve("bibliography", S1) == N


def test_a_failed_read_after_a_concurrent_write_does_not_raise(monkeypatch):
    class _Cond(Exception):
        pass

    _Cond.__name__ = "ConditionalCheckFailedException"
    table = mock.Mock()
    table.get_item.side_effect = [{}, RuntimeError("throttled")]
    table.put_item.side_effect = _Cond()
    monkeypatch.setattr(ia, "_table", lambda: table)
    monkeypatch.setattr(ia, "resolve", lambda e, i: i)
    assert ia.record("people", B, A, "dedup") is False  # said, never raised


def test_an_unreadable_survivor_chain_refuses_the_pointer(monkeypatch, caplog):
    monkeypatch.setattr(ia, "resolve", lambda e, i: None)
    with caplog.at_level(logging.ERROR):
        assert ia.record("people", B, A, "dedup") is False
    assert "unreadable" in caplog.text


def test_a_rerun_reads_each_pointer_once_and_writes_nothing(tmp_path, monkeypatch):
    from src.extraction.bibliography import record_merge_pointers

    (tmp_path / "bib_1.json").write_text(
        json.dumps({"BibliographyID": A, "merged_from": [B, C]})
    )
    assert record_merge_pointers(tmp_path) is True
    calls = []
    monkeypatch.setattr(ia, "record", lambda *a: calls.append(a) or True)
    assert record_merge_pointers(tmp_path) is True
    assert calls == []  # every id already leads to the survivor
