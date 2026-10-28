"""Provenance hardening: event SourceSectionID stamp + schema-driven completeness guard."""

import json

from src.extraction import event_provenance as EP
from src.utils import provenance_integrity as PI


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def _seed_section_and_event(
    out, eid="0W4BFG28KZ8NJM0F5MKEPMGRN8", sid="01M61D2YRNBJY2FP6YJ5XSWVEY"
):
    _write(
        out / "source_section" / f"{sid}.json",
        {
            "SourceSectionID": sid,
            "EventID": eid,
            "source": {"book": "USA-E-XChannel-8"},
        },
    )
    ev = out / "content" / "USA-E-XChannel-8" / "chapter1full-event.json"
    _write(
        ev,
        {
            "Event": {
                "EventID": eid,
                "Event_Name": "x",
                "Sub-events": [{"Sub-eventID": "4Y5QFR21HP835V12HY5Y294QJV"}],
            }
        },
    )
    return ev, eid, sid


def test_stamp_resolves_and_writes_sourcesectionid(tmp_path, monkeypatch):
    out = tmp_path
    ev, eid, sid = _seed_section_and_event(out)
    # bypass the central schema guard in the unit test (exercised separately); write raw.
    monkeypatch.setattr(
        EP, "_write_event", lambda f, d: f.write_text(json.dumps(d), encoding="utf-8")
    )
    n = EP.stamp_event_source_sections(out)
    assert n == 1
    data = json.loads(ev.read_text())
    assert data["Event"]["SourceSectionID"] == sid
    assert data["Event"]["Sub-events"][0]["SourceSectionID"] == sid


def test_stamp_noop_when_event_unknown_to_index(tmp_path):
    out = tmp_path
    # section points at a DIFFERENT event than the one on disk -> no resolvable match
    _write(
        out / "source_section" / "s.json",
        {
            "SourceSectionID": "01SECTIONXXXXXXXXXXXXXXXXX",
            "EventID": "01OTHEREVENTXXXXXXXXXXXXXX",
            "source": {"book": "b"},
        },
    )
    ev = out / "content" / "b" / "chapter1full-event.json"
    _write(ev, {"Event": {"EventID": "01REALEVENTXXXXXXXXXXXXXXX", "Sub-events": []}})
    assert EP.stamp_event_source_sections(out) == 0
    assert "SourceSectionID" not in json.loads(ev.read_text())["Event"]


def test_index_builds_from_source_section(tmp_path):
    out = tmp_path
    _write(
        out / "source_section" / "s.json",
        {"SourceSectionID": "01SID", "EventID": "01EID", "source": {"book": "b"}},
    )
    idx = EP._build_event_section_index(out)
    assert idx == {"01EID": "01SID"}


def test_guard_flags_todo_placeholder(tmp_path):
    out = tmp_path
    _write(
        out / "places" / "omaha.json",
        {
            "PlaceID": "01PLACE",
            "event_mentions": [
                {"EventID": "01EID", "author": "TODO - Add author name"}
            ],
        },
    )
    rep = PI.audit(out)
    places = next(r for r in rep["entities"] if r["entity"] == "places")
    assert places["placeholder_records"] == 1
    assert places["missing_provenance"] == 0  # it HAS a populated EventID edge


def test_guard_flags_missing_provenance(tmp_path):
    out = tmp_path
    # a place with NO event_mention / SourceSectionID / EventID at all
    _write(out / "places" / "nowhere.json", {"PlaceID": "01PLACE", "name": "nowhere"})
    rep = PI.audit(out)
    places = next(r for r in rep["entities"] if r["entity"] == "places")
    assert places["missing_provenance"] == 1


def test_guard_passes_clean_record(tmp_path):
    out = tmp_path
    _write(
        out / "dates" / "d.json",
        {"DateID": "01DATE", "event_mentions": [{"EventID": "01EID"}]},
    )
    rep = PI.audit(out)
    dates = next(r for r in rep["entities"] if r["entity"] == "dates")
    assert dates["missing_provenance"] == 0
    assert dates["placeholder_records"] == 0


def test_guard_covers_every_registry_entity(tmp_path):
    """The audit must evaluate EVERY registry entity (minus events), not just produced ones."""
    from src.schemas.entity_registry import ENTITY_REGISTRY

    rep = PI.audit(tmp_path)
    audited = {r["entity"] for r in rep["entities"]}
    expected = {s.name for s in ENTITY_REGISTRY if s.name != "events" and s.output_glob}
    assert expected.issubset(audited), expected - audited


def test_casualties_has_event_edge(tmp_path):
    """Casualties schema declares EventID/Sub-eventID (nested in its mention block) -> 'event'."""
    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    spec = next(s for s in ENTITY_REGISTRY if s.name == "casualties")
    assert PI._schema_provenance_kind(load_schema(spec)) == "event"


def test_no_registry_entity_lacks_provenance_edge(tmp_path):
    """Validated coverage: EVERY persisted registry entity declares a provenance edge (no 'none')."""
    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    gaps = []
    for spec in ENTITY_REGISTRY:
        if spec.name == "events" or not spec.output_glob:
            continue
        if PI._schema_provenance_kind(load_schema(spec)) == "none":
            gaps.append(spec.name)
    assert gaps == [], f"entities with NO provenance edge: {gaps}"


# --- Observability: the stamp + guard MUST emit canonical OBSERV lines ---


def test_stamp_emits_observ(tmp_path, monkeypatch, caplog):
    import logging

    out = tmp_path
    _seed_section_and_event(out)
    monkeypatch.setattr(
        EP, "_write_event", lambda f, d: f.write_text(json.dumps(d), encoding="utf-8")
    )
    with caplog.at_level(logging.INFO, logger=EP.__name__):
        EP.stamp_event_source_sections(out)
    assert "OBSERV event_provenance.stamp" in caplog.text
    assert "stamped=1" in caplog.text


def test_guard_run_for_phase_emits_observ(tmp_path, caplog):
    import logging

    out = tmp_path
    # one placeholder record (anomaly) + one clean record
    _write(
        out / "places" / "p.json",
        {
            "PlaceID": "01P",
            "event_mentions": [{"EventID": "01E", "author": "TODO - Add author name"}],
        },
    )
    with caplog.at_level(logging.INFO, logger=PI.__name__):
        PI.run_for_phase(out, phase="Phase 3 (local)")
    # summary line always fires
    assert "OBSERV provenance.audit phase=Phase 3 (local)" in caplog.text
    # per-entity anomaly line fires for the placeholder record
    assert "OBSERV provenance.entity entity=places" in caplog.text
    assert "placeholders=1" in caplog.text


def test_stamp_resolves_nested_source_section(tmp_path, monkeypatch):
    """E2E-caught: source_section records live at content/<book>/source_section/, NOT top-level.
    The index + stamp must resolve them there."""
    out = tmp_path
    eid = "0W4BFG28KZ8NJM0F5MKEPMGRN8"
    sid = "01M624480WP26S9QE9XND5CB20"
    _write(
        out / "content" / "USA-E-XChannel-8" / "source_section" / f"{sid}.json",
        {
            "SourceSectionID": sid,
            "EventID": eid,
            "source": {"book": "USA-E-XChannel-8"},
        },
    )
    ev = out / "content" / "USA-E-XChannel-8" / "chapter1full-event.json"
    _write(
        ev,
        {
            "Event": {
                "EventID": eid,
                "Sub-events": [{"Sub-eventID": "4Y5QFR21HP835V12HY5Y294QJV"}],
            }
        },
    )
    assert EP._build_event_section_index(out) == {eid: sid}
    monkeypatch.setattr(
        EP, "_write_event", lambda f, d: f.write_text(json.dumps(d), encoding="utf-8")
    )
    assert EP.stamp_event_source_sections(out) == 1
    data = json.loads(ev.read_text())
    assert data["Event"]["SourceSectionID"] == sid
    assert data["Event"]["Sub-events"][0]["SourceSectionID"] == sid
