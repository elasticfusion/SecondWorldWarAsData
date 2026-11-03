"""DRY P1.10 guard: all three schema-key coercers (casualties, supplemental, people_groups) delegate
to the single schemas.strip_unmodeled_keys helper. people_groups previously inlined its own strip
loop (which also ignored patternProperties); it now shares the canonical helper. These tests pin the
delegation + behavior-preserving strip.
"""

from unittest import mock

import src.schemas as schemas


def test_people_groups_coerce_delegates(monkeypatch):
    import src.extraction.people_groups as pg

    calls = []
    monkeypatch.setattr(
        schemas, "strip_unmodeled_keys", lambda rec, sch, cb: calls.append((rec, sch))
    )
    group = {"group_name": "1st Division", "bogus_field": 1}
    pg._coerce_group_schema_keys(group)
    assert len(calls) == 1 and calls[0][0] is group  # delegated with the group record


def test_casualties_coerce_delegates(monkeypatch):
    import src.extraction.casualties as cas

    calls = []
    monkeypatch.setattr(
        schemas, "strip_unmodeled_keys", lambda rec, sch, cb: calls.append(rec)
    )
    item = {"type": "KIA", "bogus": 1}
    cas._coerce_casualty_schema_keys(item)
    assert calls == [item]


def test_supplemental_coerce_delegates(monkeypatch):
    import src.extraction.supplemental as sup

    calls = []
    monkeypatch.setattr(
        schemas, "strip_unmodeled_keys", lambda rec, sch, cb: calls.append(rec)
    )
    data = {"Sub-eventID": "X", "bogus": 1}
    sup._coerce_supplemental_schema_keys(data)
    assert calls == [data]


def test_people_groups_strips_unmodeled_key_behaviorally():
    # End-to-end (no mock): an unmodeled top-level key is removed, modeled keys survive.
    import src.extraction.people_groups as pg

    group = {"group_name": "1st Infantry Division", "definitely_not_a_schema_field": 99}
    pg._coerce_group_schema_keys(group)
    assert "definitely_not_a_schema_field" not in group
    assert group["group_name"] == "1st Infantry Division"  # modeled key preserved
