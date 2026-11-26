"""Schema version steps + bring_forward (owner 2026-11-25, D2).

Every entity version change is declared (src/schemas/version_steps), and a record is brought to
the current version in memory before a merge or update: forward-compatible steps on the fly, a
breaking step waits for the upgrade release, and the result is checked against the current
schema - so a record the write guard let through untyped (its lenient old-version path) is caught.
"""

import pytest

from src.schemas import ENTITY_SCHEMA_VERSIONS, entity_version, version_steps
from src.schemas import schema_contract as SC
from src.schemas.entity_registry import ENTITY_REGISTRY

BIB = "bibliography"


def _bib(version, **extra):
    return {
        "BibliographyID": "01M00000000000000000000001",
        "title": "Utah Beach to Cherbourg",
        "_schema_version": version,
        **extra,
    }


# --- the declarations (D2-6: a bump without its step fails here) ----------------------------


@pytest.mark.parametrize("entity", sorted(ENTITY_SCHEMA_VERSIONS))
def test_every_entity_declares_its_steps_up_to_the_current_version(entity):
    versions = version_steps.history(entity)
    assert versions, f"{entity}: no version history in src/schemas/version_steps.py"
    assert versions[0] == version_steps.FIRST_VERSION
    assert versions[-1] == ENTITY_SCHEMA_VERSIONS[entity], (
        f"{entity} is at {ENTITY_SCHEMA_VERSIONS[entity]} but its declared steps end at "
        f"{versions[-1]}: add the step (and its kind) to src/schemas/version_steps.py"
    )
    tuples = [SC._version_tuple(v) for v in versions]
    assert tuples == sorted(set(tuples)), f"{entity}: versions must strictly increase"


def test_every_registered_entity_is_versioned():
    assert {s.name for s in ENTITY_REGISTRY} <= set(ENTITY_SCHEMA_VERSIONS)


def test_breaking_steps_and_transforms_name_declared_steps():
    declared = {
        (e, a, b) for e in ENTITY_SCHEMA_VERSIONS for a, b, _ in version_steps.steps(e)
    }
    assert set(version_steps._BREAKING) <= declared
    assert set(version_steps.TRANSFORMS) <= declared


# --- bring_forward ---------------------------------------------------------------------------


def test_a_current_record_is_current():
    out = SC.bring_forward(_bib(entity_version(BIB)), BIB)
    assert (out.status, out.usable) == (SC.CURRENT, True)


def test_an_older_valid_record_is_brought_forward_and_stamped_on_a_copy():
    old = _bib("2.29")
    out = SC.bring_forward(old, BIB)
    assert out.status == SC.BROUGHT_FORWARD and out.usable
    assert out.record["_schema_version"] == entity_version(BIB)
    assert old["_schema_version"] == "2.29"  # the input is never changed


def test_an_old_record_of_the_wrong_shape_is_invalid():
    """The write guard lets an old-version record through without type checks: caught here."""
    out = SC.bring_forward(_bib("2.29", title=5), BIB)
    assert out.status == SC.INVALID and not out.usable
    assert out.detail == "title type"  # path + keyword, never the value


@pytest.mark.parametrize(
    "data,status,detail",
    [
        ([1, 2], SC.INVALID, "not a JSON object"),
        (_bib("two.nine"), SC.INVALID, "malformed _schema_version"),
        (_bib("2.33"), SC.FUTURE, "newer than"),
        (_bib("2.23"), SC.NEEDS_RELEASE_UPGRADE, "predates 2.24"),
        (_bib("1.0"), SC.NEEDS_RELEASE_UPGRADE, "predates 2.24"),
    ],
)
def test_records_that_cannot_be_used_say_why(data, status, detail):
    out = SC.bring_forward(data, BIB)
    assert out.status == status and detail in out.detail and not out.usable


def test_an_undeclared_version_is_invalid():
    # equipment went 2.28 -> 2.30: 2.29 was never an equipment version
    out = SC.bring_forward(
        {"EquipmentID": "01M00000000000000000000001", "_schema_version": "2.29"},
        "equipment",
    )
    assert out.status == SC.INVALID and "never a equipment version" in out.detail


def test_a_record_with_no_version_is_checked_against_the_current_schema():
    rec = _bib(None)
    del rec["_schema_version"]
    assert SC.bring_forward(rec, BIB).status == SC.BROUGHT_FORWARD
    assert SC.bring_forward({**rec, "title": ["x"]}, BIB).status == SC.INVALID


def test_a_breaking_step_waits_for_the_release(monkeypatch):
    monkeypatch.setattr(version_steps, "_BREAKING", frozenset({(BIB, "2.30", "2.31")}))
    out = SC.bring_forward(_bib("2.29"), BIB)
    assert out.status == SC.NEEDS_RELEASE_UPGRADE and "2.30 -> 2.31" in out.detail
    # a record already past the breaking step is not held by it
    assert SC.bring_forward(_bib("2.31"), BIB).status == SC.BROUGHT_FORWARD


def test_a_forward_transform_runs_on_its_step_only(monkeypatch):
    seen = []

    def add_flag(rec):
        seen.append(rec["_schema_version"])
        rec["merged_from"] = []
        return rec

    monkeypatch.setitem(version_steps.TRANSFORMS, (BIB, "2.31", "2.32"), add_flag)
    out = SC.bring_forward(_bib("2.29"), BIB)
    assert out.usable and out.record["merged_from"] == [] and len(seen) == 1
    assert "merged_from" not in SC.bring_forward(_bib("2.32"), BIB).record


def test_a_transform_that_fails_makes_the_record_invalid(monkeypatch):
    def boom(rec):
        raise KeyError("x")

    monkeypatch.setitem(version_steps.TRANSFORMS, (BIB, "2.30", "2.31"), boom)
    out = SC.bring_forward(_bib("2.29"), BIB)
    assert out.status == SC.INVALID and out.detail == "step 2.30 -> 2.31: KeyError"


@pytest.mark.parametrize("data", [{"x": 1}, {"_schema_version": "2.30"}, [1]])
def test_an_unknown_entity_is_a_programming_error(data):
    with pytest.raises(ValueError):
        SC.bring_forward(data, "no_such_entity")


@pytest.mark.parametrize(
    "version,status",
    [
        ("2.3", SC.NEEDS_RELEASE_UPGRADE),  # not 2.30
        ("2.24.1", SC.INVALID),
        ("2.32.0", SC.INVALID),
        ("2.032", SC.INVALID),
        (" 2.32 ", SC.CURRENT),  # whitespace stripped, like the write guard
        (2.32, SC.INVALID),  # not a string
        (["2.32"], SC.INVALID),
    ],
)
def test_version_edge_cases(version, status):
    assert SC.bring_forward(_bib(version), BIB).status == status


@pytest.mark.parametrize(
    "version", ["9\n.1 \n", "9" * 4000 + ".1", "2.32\nOBSERV fake status=ok", "２.３２"]
)
def test_a_version_never_reaches_the_detail(version):
    """A record's version is shown only after it is checked to be digits + a dot."""
    out = SC.bring_forward(_bib(version), BIB)
    assert out.status == SC.INVALID and out.detail == "malformed _schema_version"


def test_a_record_key_cannot_forge_a_log_line_or_grow_the_detail():
    out = SC.bring_forward(_bib("2.32", **{"evil\nOBSERV fake status=ok": 1}), BIB)
    assert out.status == SC.INVALID
    assert "\n" not in out.detail and "<key>" in out.detail
    long_key = SC.bring_forward(_bib("2.32", **{"k" * 5000: 1}), BIB)
    assert len(long_key.detail) <= 200


def test_the_detail_names_the_field():
    rec = _bib("2.32")
    del rec["title"]
    assert SC.bring_forward(rec, BIB).detail == "<root> required title"
    extra = SC.bring_forward(_bib("2.32", zzz=1), BIB)
    assert extra.detail == "<root> additionalProperties zzz"


def test_keys_a_schema_allows_by_pattern_are_never_named():
    """patternProperties-allowed keys (e.g. "^_" metadata) are legal: only the real offender is named
    (no entity schema uses patternProperties at its top level today; nested/future ones may).
    """
    from types import SimpleNamespace

    err = SimpleNamespace(
        validator="additionalProperties",
        validator_value=False,
        instance={"_a": 1, "_b": 2, "_c": 3, "title": "t", "zzz": 1},
        schema={"properties": {"title": {}}, "patternProperties": {"^_": {}}},
    )
    assert SC._named_fields(err) == ["zzz"]


def test_a_deeply_nested_record_is_invalid_not_a_crash():
    deep: dict = {}
    node = deep
    for _ in range(5000):
        node["x"] = {}
        node = node["x"]
    out = SC.bring_forward(_bib("2.32", citation=deep), BIB)
    assert out.status == SC.INVALID and not out.usable


@pytest.mark.parametrize(
    "version", ["2.32", "2.29", "2.23", "2.33", "nope", "2.29-bad-title"]
)
def test_the_record_is_always_a_copy(version, monkeypatch):
    data = _bib(version.replace("-bad-title", ""), citation={"title": "x"})
    if version.endswith("bad-title"):
        data["title"] = 5
    out = SC.bring_forward(data, BIB)
    assert out.record is not data
    if isinstance(out.record, dict) and isinstance(out.record.get("citation"), dict):
        out.record["citation"]["title"] = "changed"
        assert data["citation"]["title"] == "x"  # nested independence


def test_a_failed_transform_returns_a_copy(monkeypatch):
    monkeypatch.setitem(
        version_steps.TRANSFORMS,
        (BIB, "2.30", "2.31"),
        lambda rec: (_ for _ in ()).throw(KeyError("x")),
    )
    data = _bib("2.29")
    out = SC.bring_forward(data, BIB)
    assert out.status == SC.INVALID and out.record is not data


def test_say_unusable_is_one_actionable_line(caplog):
    import logging

    log = logging.getLogger("t")
    with caplog.at_level(logging.WARNING):
        SC.say_unusable(log, BIB, "bib_x.json", SC.bring_forward(_bib("2.32"), BIB))
        assert not caplog.records  # usable: nothing said
        SC.say_unusable(
            log, BIB, "bad\nname.json", SC.bring_forward(_bib("2.29", title=5), BIB)
        )
    (rec,) = caplog.records
    msg = rec.getMessage()
    assert "OBSERV schema.not_usable entity=bibliography" in msg
    assert "status=invalid detail=title type" in msg and "fix the field named" in msg
    assert "\n" not in msg
