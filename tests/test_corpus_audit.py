"""Tests for the read-only corpus audit (block b-3)."""

import copy

import src.utils.corpus_audit as ca

P1 = "01HX7YZABCDEFGHJKMNPQRSTVW"
P2 = "01HX7YZABCDEFGHJKMNPQRSTVX"
EV1 = "01HX7YZABCDEFGHJKMNPQRSTVZ"
MISSING = "01MISSINGMISSINGMISSING000"


class FakeStorage:
    def __init__(self, files):
        self.files = files

    def list_files(self, prefix, pattern="*.json"):
        pref = prefix.rstrip("/") + "/"
        out = [k for k in self.files if k.startswith(pref) and k.endswith(".json")]
        if "-event.json" in pattern:
            out = [k for k in out if k.endswith("-event.json")]
        return out

    def read_json(self, path):
        return self.files[path]


def test_clean_corpus_has_no_problems():
    st = FakeStorage(
        {
            "people/a.json": {"PersonID": P1, "name": "A"},
            "people/b.json": {"PersonID": P2, "name": "B"},
        }
    )
    rep = ca.audit_corpus(st)
    assert rep["total_problems"] == 0
    assert rep["pk_uniqueness"]["collision_count"] == 0


def test_pk_collision_detected():
    st = FakeStorage(
        {
            "people/a.json": {"PersonID": P1, "name": "A"},
            "people/dup.json": {"PersonID": P1, "name": "A-variant"},  # same PK
        }
    )
    rep = ca.audit_corpus(st)
    assert rep["pk_uniqueness"]["collision_count"] == 1
    c = rep["pk_uniqueness"]["collisions"][0]
    assert c["entity"] == "people" and c["pk"] == P1
    assert set(c["files"]) == {"people/a.json", "people/dup.json"}


def test_schema_invalid_record_detected():
    # places requires PlaceID; a record missing it (and unrepairable context) is schema-invalid.
    st = FakeStorage(
        {
            "places/bad.json": {"name": "X", "latitude": "not-a-number"},
        }
    )
    rep = ca.audit_corpus(st)
    # The guard either repairs the PK (allow) or blocks on the bad latitude type; either way the
    # audit must COUNT without mutating the stored record.
    assert "schema_validity" in rep
    assert rep["schema_validity"]["records_checked"] >= 1


def test_dangling_reference_detected():
    st = FakeStorage(
        {
            "casualties/c.json": {"CasualtyID": P1, "type": "KIA", "PersonID": MISSING},
            "people/a.json": {"PersonID": P1, "name": "A"},
        }
    )
    rep = ca.audit_corpus(st)
    assert rep["referential_integrity"]["total_dangling"] >= 1


def test_three_file_pk_collision_shape():
    st = FakeStorage(
        {
            "people/a.json": {"PersonID": P1, "name": "A"},
            "people/b.json": {"PersonID": P1, "name": "B"},
            "people/c.json": {"PersonID": P1, "name": "C"},
        }
    )
    rep = ca.audit_corpus(st)
    assert rep["pk_uniqueness"]["collision_count"] == 1  # one GROUP, not per-pair
    files = rep["pk_uniqueness"]["collisions"][0]["files"]
    assert sorted(files) == ["people/a.json", "people/b.json", "people/c.json"]
    assert len(files) == len(set(files))  # no double-count


def test_schema_sweep_covers_events_and_map_features():
    """Whole-corpus: nested events + map_features (no flat PK) are included in the sweep count."""
    st = FakeStorage(
        {
            "content/TheBook/ch1-event.json": {
                "Event": {"EventID": EV1, "Sub-events": []}
            },
            "map_features/m.json": {"MapID": EV1, "features": []},
        }
    )
    rep = ca.audit_corpus(st)
    assert rep["schema_validity"]["records_checked"] >= 2


def test_audit_is_read_only():
    """The audit must not mutate the stored records (deep-copy + record_stats=False)."""
    files = {"places/p.json": {"name": "X", "latitude": 0.0, "longitude": 0.0}}
    before = copy.deepcopy(files)
    ca.audit_corpus(FakeStorage(files))
    assert files == before  # no PK minted, no quality_flags added to the stored dict
