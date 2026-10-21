"""§1.0d CRITICAL regression: a dedup merge deletes the secondary entity file but MUST rebase
its references in event SUB-EVENT arrays (places/groups/equipment/people), not just in other
entity files. Two bugs lived in merge._merge_one_secondary:
  (1) people_groups used the PeopleGroupID alias instead of the record's real PK (GroupID), so
      old/new id resolved to "" and the rebase was skipped entirely.
  (2) merge_generic passed the PK field-NAME ("PlaceID") as the sub-event ref_key, but sub-events
      store refs under the PLURAL key ("places"), so se.get(ref_key) was always empty and the
      sub-event arrays were never repointed.
Also covers: the rebase happens BEFORE the secondary is deleted, and an OBSERV warning fires when
a merge repoints 0 refs (a future ref_key/PK mismatch stays visible).
"""

import json
import logging

from src.dedup.merge import merge_generic

PRIMARY_PLACE = "01HX7YZABCDEFGHJKMNPQRSTAA"
SECONDARY_PLACE = "01HX7YZABCDEFGHJKMNPQRSTAB"
PRIMARY_GROUP = "01HX7YZABCDEFGHJKMNPQRSTAC"
SECONDARY_GROUP = "01HX7YZABCDEFGHJKMNPQRSTAD"


def _write_event(output_root, ref_key, ids):
    content = output_root / "content" / "book1"
    content.mkdir(parents=True, exist_ok=True)
    event = {
        "Event": {
            "EventID": "01HX7YZABCDEFGHJKMNPQRSTAE",
            "Sub-events": [
                {"Sub-eventID": "01HX7YZABCDEFGHJKMNPQRSTAF", ref_key: list(ids)}
            ],
        }
    }
    (content / "ch01-event.json").write_text(json.dumps(event), encoding="utf-8")
    return content / "ch01-event.json"


def _subevent_refs(event_file, ref_key):
    d = json.loads(event_file.read_text(encoding="utf-8"))
    return d["Event"]["Sub-events"][0][ref_key]


def test_place_merge_repoints_subevent_places_array(tmp_path):
    """CR#2: a place merge repoints the event sub-event places[] array (old PlaceID -> primary)."""
    places = tmp_path / "places"
    places.mkdir()
    (places / "bastogne.json").write_text(
        json.dumps(
            {"PlaceID": PRIMARY_PLACE, "name": "Bastogne", "event_mentions": []}
        ),
        encoding="utf-8",
    )
    (places / "bastonge.json").write_text(
        json.dumps(
            {"PlaceID": SECONDARY_PLACE, "name": "Bastonge", "event_mentions": []}
        ),
        encoding="utf-8",
    )
    event_file = _write_event(tmp_path, "places", [SECONDARY_PLACE, "OTHER"])

    people = [
        {"filename": "bastogne.json", "name": "Bastogne"},
        {"filename": "bastonge.json", "name": "Bastonge"},
    ]
    merge_generic(places, people, 0, id_field="PlaceID")

    refs = _subevent_refs(event_file, "places")
    assert PRIMARY_PLACE in refs, "secondary PlaceID was not repointed to the primary"
    assert SECONDARY_PLACE not in refs, "dangling secondary PlaceID remains"
    assert "OTHER" in refs
    assert not (places / "bastonge.json").exists()  # secondary deleted


def test_group_merge_uses_groupid_pk_and_repoints_groups_array(tmp_path):
    """CR#1: people_groups records are keyed on GroupID (PeopleGroupID is only the referrer
    alias). Even when the caller passes the alias as id_field, the merge must resolve the real
    PK and repoint the sub-event groups[] array."""
    groups = tmp_path / "people_groups"
    groups.mkdir()
    (groups / "ab.json").write_text(
        json.dumps(
            {"GroupID": PRIMARY_GROUP, "group_name": "A Btln", "event_mentions": []}
        ),
        encoding="utf-8",
    )
    (groups / "ab2.json").write_text(
        json.dumps(
            {"GroupID": SECONDARY_GROUP, "group_name": "A Bn", "event_mentions": []}
        ),
        encoding="utf-8",
    )
    event_file = _write_event(tmp_path, "peoplegroups", [SECONDARY_GROUP])

    people = [
        {"filename": "ab.json", "name": "A Btln"},
        {"filename": "ab2.json", "name": "A Bn"},
    ]
    # Pass the ALIAS id_field on purpose — the record PK is GroupID.
    merge_generic(groups, people, 0, id_field="PeopleGroupID")

    # Group refs are stored on disk under the "peoplegroups" sub-event key (batch_parallel),
    # NOT the schema's "groups" property — the rebase must use the on-disk key.
    refs = _subevent_refs(event_file, "peoplegroups")
    assert refs == [
        PRIMARY_GROUP
    ], f"peoplegroups[] not repointed to primary GroupID: {refs}"
    assert not (groups / "ab2.json").exists()


def test_rebase_happens_before_secondary_delete(tmp_path):
    """The event sub-event ref must already point to the primary by the time the secondary file
    is gone — i.e. refs are rebased before the unlink (no window where the ref dangles).
    """
    places = tmp_path / "places"
    places.mkdir()
    (places / "p1.json").write_text(
        json.dumps({"PlaceID": PRIMARY_PLACE, "name": "P1", "event_mentions": []}),
        encoding="utf-8",
    )
    (places / "p2.json").write_text(
        json.dumps({"PlaceID": SECONDARY_PLACE, "name": "P2", "event_mentions": []}),
        encoding="utf-8",
    )
    event_file = _write_event(tmp_path, "places", [SECONDARY_PLACE])

    import src.dedup.merge as m

    order = []
    real_unlink = m.Path.unlink

    def spy_unlink(self, *a, **k):
        # Record the event-ref state at the moment the secondary file is unlinked.
        if self.name == "p2.json":
            order.append(("unlink", _subevent_refs(event_file, "places")))
        return real_unlink(self, *a, **k)

    m.Path.unlink = spy_unlink
    try:
        people = [
            {"filename": "p1.json", "name": "P1"},
            {"filename": "p2.json", "name": "P2"},
        ]
        merge_generic(places, people, 0, id_field="PlaceID")
    finally:
        m.Path.unlink = real_unlink

    assert order, "secondary file was never unlinked"
    _, refs_at_delete = order[0]
    assert refs_at_delete == [PRIMARY_PLACE], (
        "refs were NOT rebased before the secondary delete: %s" % refs_at_delete
    )


def test_people_path_still_rebases(tmp_path):
    """Regression guard: the people path (do_merge, literal ref_key='people') still repoints
    the sub-event people[] array."""
    from src.dedup.merge import do_merge

    people_dir = tmp_path / "people"
    people_dir.mkdir()
    (people_dir / "index.json").write_text("{}", encoding="utf-8")
    prim_id = "01HX7YZABCDEFGHJKMNPQRSTAG"
    sec_id = "01HX7YZABCDEFGHJKMNPQRSTAH"
    (people_dir / "a.json").write_text(
        json.dumps({"PersonID": prim_id, "name": "A", "event_mentions": []}),
        encoding="utf-8",
    )
    (people_dir / "b.json").write_text(
        json.dumps({"PersonID": sec_id, "name": "A.", "event_mentions": []}),
        encoding="utf-8",
    )
    event_file = _write_event(tmp_path, "people", [sec_id])

    do_merge(
        people_dir,
        [{"filename": "a.json", "name": "A"}, {"filename": "b.json", "name": "A."}],
        0,
    )
    assert _subevent_refs(event_file, "people") == [prim_id]


def test_equipment_merge_rebases_cross_entity_no_zero_warning(tmp_path, caplog):
    """Equipment has NO sub-event ref array, but an equipment merge must still rebase
    EquipmentID cross-refs held inside OTHER entity records — and must NOT emit a spurious
    dedup.rebase.zero warning (equipment legitimately has no sub-event refs)."""
    equip = tmp_path / "equipment"
    equip.mkdir()
    prim = "01HX7YZABCDEFGHJKMNPQRSTEA"
    sec = "01HX7YZABCDEFGHJKMNPQRSTEB"
    (equip / "e1.json").write_text(
        json.dumps({"EquipmentID": prim, "common_name": "M4", "event_mentions": []}),
        encoding="utf-8",
    )
    (equip / "e2.json").write_text(
        json.dumps({"EquipmentID": sec, "common_name": "M4A1", "event_mentions": []}),
        encoding="utf-8",
    )
    # A logistics record holds an EquipmentID cross-ref to the secondary.
    logi = tmp_path / "logistics"
    logi.mkdir()
    (logi / "supply.json").write_text(
        json.dumps(
            {
                "LogisticsID": "01HX7YZABCDEFGHJKMNPQRSTEC",
                "logistics_type": "supply",
                "event_mentions": [
                    {"MentionID": "01HX7YZABCDEFGHJKMNPQRSTED", "EquipmentID": sec}
                ],
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="src.dedup.merge"):
        merge_generic(
            equip,
            [
                {"filename": "e1.json", "name": "M4"},
                {"filename": "e2.json", "name": "M4A1"},
            ],
            0,
            id_field="EquipmentID",
        )

    # Cross-entity EquipmentID repointed in the logistics record.
    logi_rec = json.loads((logi / "supply.json").read_text(encoding="utf-8"))
    assert logi_rec["event_mentions"][0]["EquipmentID"] == prim
    # No spurious zero-rebase warning for equipment (it has no sub-event ref array).
    assert not any("dedup.rebase.zero" in r.message for r in caplog.records)


def test_zero_ref_rebase_logs_observ(tmp_path, caplog):
    """A merge that repoints 0 event refs must emit an OBSERV warning so a future ref_key/PK
    mismatch is visible (here: no event file references the secondary at all)."""
    places = tmp_path / "places"
    places.mkdir()
    (places / "p1.json").write_text(
        json.dumps({"PlaceID": PRIMARY_PLACE, "name": "P1", "event_mentions": []}),
        encoding="utf-8",
    )
    (places / "p2.json").write_text(
        json.dumps({"PlaceID": SECONDARY_PLACE, "name": "P2", "event_mentions": []}),
        encoding="utf-8",
    )
    # An event that references NOBODY relevant.
    _write_event(tmp_path, "places", ["UNRELATED"])

    with caplog.at_level(logging.WARNING, logger="src.dedup.merge"):
        merge_generic(
            places,
            [
                {"filename": "p1.json", "name": "P1"},
                {"filename": "p2.json", "name": "P2"},
            ],
            0,
            id_field="PlaceID",
        )
    assert any(
        "dedup.rebase.zero" in r.message for r in caplog.records
    ), "expected an OBSERV dedup.rebase.zero warning"
