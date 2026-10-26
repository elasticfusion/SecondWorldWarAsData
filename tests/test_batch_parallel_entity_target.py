"""Regression for the SCHEMA_TARGET cross-entity bug: _add_event_mention_batch used a single
events-version (2.24) target for EVERY entity, so a newer-entity record (places 2.29, groups 2.28)
was classified 'future schema' and SKIPPED -> its event_mention backlink was dropped -> dangling
cross-references. The fix resolves the target PER-ENTITY from the record's directory."""

from pathlib import Path
import src.extraction.batch_parallel as B
from src.schemas import entity_version


def test_entity_target_is_per_entity_not_events():
    # Each entity resolves to ITS OWN version (not the events 2.24 that wrongly gated them).
    assert B._entity_target_for(Path("output/places/x.json")) == entity_version(
        "places"
    )
    assert B._entity_target_for(Path("output/people_groups/g.json")) == entity_version(
        "people_groups"
    )
    assert B._entity_target_for(Path("output/people/p.json")) == entity_version(
        "people"
    )
    # places/groups are NEWER than events -> the old global target would have skipped them
    assert entity_version("places") > entity_version("events")
    assert entity_version("people_groups") > entity_version("events")


def test_unknown_entity_falls_back_safely():
    # An unrecognized dir must not raise; falls back to the events (min) version.
    assert B._entity_target_for(Path("output/zzz_unknown/x.json")) == entity_version(
        "events"
    )


def test_places_record_not_classified_future():
    """A current places record read against its OWN target is NOT 'future' (would have been skipped
    against the events 2.24 target)."""
    from src.schemas.schema_contract import read_for_update
    import logging

    rec = {
        "PlaceID": "01HX7YZABCDEFGHJKMNPQRSTVW",
        "name": "Aachen",
        "_schema_version": entity_version("places"),
    }
    target = B._entity_target_for(Path("output/places/aachen.json"))
    _data, skip = read_for_update(rec, target, logging.getLogger("t"), "aachen.json")
    assert (
        skip is False
    )  # NOT skipped as future -> its mention WILL be added (no dangler)
