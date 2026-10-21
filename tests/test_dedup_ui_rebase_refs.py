"""§1.0d CRITICAL path 3: the Lambda dedup-UI merge must rebase event sub-event refs.
Previously _merge_generic_files rebased NOTHING (every human-approved places/groups/equipment
merge orphaned all refs to the merged-away entity), and _merge_people_files ran do_merge on a
people-only tmpdir whose update_event_refs found no event files (so people merges didn't rebase
either). These tests drive the handler's merge functions against an in-memory storage and assert
the event corpus is repointed old -> primary.
"""

import json

import pytest

import lambda_handlers.dedup_ui_handler as ui


class FakeStorage:
    """In-memory storage modelling the dedup-UI handler's S3Storage, which is constructed with
    an EMPTY prefix (lambda_handlers/dedup_ui_handler.py builds S3Storage(bucket, region)). So
    every key is a full 'output/...' path, list_files returns those full keys unchanged, and it
    is RECURSIVE (S3 prefix listing), matching production. (The old version modelled a
    prefix='output' storage the handler never builds, which hid a real prefix bug.)"""

    def __init__(self):
        self.data = {}

    def read_json(self, path):
        if path not in self.data:
            raise KeyError(path)
        return json.loads(json.dumps(self.data[path]))  # deep copy

    def write_json(self, path, data):
        self.data[path] = json.loads(json.dumps(data))

    def delete(self, path):
        self.data.pop(path, None)

    def list_files(self, prefix, pattern="*.json"):
        from fnmatch import fnmatch

        pre = prefix.rstrip("/") + "/"
        out = []
        for k in self.data:
            if not k.startswith(pre):
                continue
            if fnmatch(k.split("/")[-1], pattern):
                out.append(k)  # full key, empty-prefix storage
        return sorted(out)


@pytest.fixture(autouse=True)
def _no_manifest(monkeypatch):
    # _append_to_manifest / _sync_entity_to_dynamo hit AWS — stub them out.
    monkeypatch.setattr(ui, "_append_to_manifest", lambda keys: None)
    monkeypatch.setattr(ui, "_sync_entity_to_dynamo", lambda t, d: None)
    monkeypatch.setattr(ui, "_lambda_context", None)


PRIM = "01HX7YZABCDEFGHJKMNPQRSTBA"
SEC = "01HX7YZABCDEFGHJKMNPQRSTBB"


def _event(ref_key, ids):
    return {
        "Event": {
            "EventID": "01HX7YZABCDEFGHJKMNPQRSTBE",
            "Sub-events": [
                {"Sub-eventID": "01HX7YZABCDEFGHJKMNPQRSTBF", ref_key: list(ids)}
            ],
        }
    }


def test_ui_generic_place_merge_rebases_event_refs():
    st = FakeStorage()
    st.write_json(
        "output/places/p1.json",
        {"PlaceID": PRIM, "name": "P1", "event_mentions": [], "aliases": []},
    )
    st.write_json(
        "output/places/p2.json",
        {"PlaceID": SEC, "name": "P2", "event_mentions": []},
    )
    st.write_json("output/content/book1/ch01-event.json", _event("places", [SEC, "X"]))

    ui._merge_generic_files(
        "places",
        [{"filename": "p1.json", "name": "P1"}, {"filename": "p2.json", "name": "P2"}],
        0,
        st,
    )

    ev = st.read_json("output/content/book1/ch01-event.json")
    refs = ev["Event"]["Sub-events"][0]["places"]
    assert PRIM in refs and SEC not in refs and "X" in refs
    assert "output/places/p2.json" not in st.data  # secondary deleted


def test_ui_group_merge_uses_groupid_and_rebases_groups_array():
    st = FakeStorage()
    st.write_json(
        "output/people_groups/g1.json",
        {"GroupID": PRIM, "group_name": "G1", "event_mentions": [], "aliases": []},
    )
    st.write_json(
        "output/people_groups/g2.json",
        {"GroupID": SEC, "group_name": "G2", "event_mentions": []},
    )
    st.write_json("output/content/book1/ch01-event.json", _event("peoplegroups", [SEC]))

    ui._merge_generic_files(
        "groups",
        [{"filename": "g1.json", "name": "G1"}, {"filename": "g2.json", "name": "G2"}],
        0,
        st,
    )

    ev = st.read_json("output/content/book1/ch01-event.json")
    # Group refs live under the on-disk "peoplegroups" sub-event key.
    assert ev["Event"]["Sub-events"][0]["peoplegroups"] == [PRIM]


def test_ui_people_merge_rebases_people_array():
    st = FakeStorage()
    st.write_json(
        "output/people/a.json", {"PersonID": PRIM, "name": "A", "event_mentions": []}
    )
    st.write_json(
        "output/people/b.json", {"PersonID": SEC, "name": "A.", "event_mentions": []}
    )
    st.write_json("output/people/index.json", {})
    st.write_json("output/content/book1/ch01-event.json", _event("people", [SEC]))

    err = ui._merge_people_files(
        [{"filename": "a.json", "name": "A"}, {"filename": "b.json", "name": "A."}],
        0,
        st,
    )
    assert err is None

    ev = st.read_json("output/content/book1/ch01-event.json")
    assert ev["Event"]["Sub-events"][0]["people"] == [PRIM]
    assert "output/people/b.json" not in st.data
