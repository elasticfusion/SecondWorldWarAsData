"""Tests for the corpus referential-integrity audit (read-only, offline)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ria",
    str(
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "referential_integrity_audit.py"
    ),
)
ria = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ria)

P1 = "01HX7YZABCDEFGHJKMNPQRSTVW"
P2 = "01HX7YZABCDEFGHJKMNPQRSTVX"
G1 = "01HX7YZABCDEFGHJKMNPQRSTVY"
EV1 = "01HX7YZABCDEFGHJKMNPQRSTVZ"
MISSING = "01MISSINGMISSINGMISSING000"


class FakeStorage:
    """Minimal Storage: files keyed by 'dir/name.json'."""

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


def _corpus():
    return {
        # people
        "people/a.json": {
            "PersonID": P1,
            "name": "A",
            "group_affiliations": [{"GroupID": G1}],
        },
        "people/b.json": {
            "PersonID": P2,
            "name": "B",
            "group_affiliations": [{"GroupID": MISSING}],
        },
        # groups
        "people_groups/g.json": {"GroupID": G1, "group_name": "1st Div"},
        # events (nested-style path simulated flat under content/)
        "content/TheBook/ch1a-event.json": {
            "Event": {"EventID": EV1, "Sub-events": [{"Sub-eventID": P2}]}
        },
        # casualties: one resolvable PersonID, one dangling, event_context resolvable
        "casualties/c1.json": {
            "CasualtyID": P1,
            "type": "KIA",
            "PersonID": P1,
            "event_context": {"EventID": EV1},
        },
        "casualties/c2.json": {"CasualtyID": P2, "type": "WIA", "PersonID": MISSING},
        # weather: dangling PlaceID (the type-confusion class)
        "weather/w.json": {
            "WeatherID": P1,
            "date": "1944-12-16",
            "location": {"place_name": "X", "PlaceID": MISSING},
        },
    }


def test_pk_sets_collects_pks_and_events():
    st = FakeStorage(_corpus())
    sets = ria.build_pk_sets(st)
    assert P1 in sets["people"] and P2 in sets["people"]
    assert G1 in sets["groups"]
    assert EV1 in sets["events"] and P2 in sets["events"]  # EventID + Sub-eventID


def test_audit_counts_dangling_and_resolvable():
    st = FakeStorage(_corpus())
    rep = ria.audit(st)
    by_edge = {e["edge"]: e for e in rep["edges"]}
    # people.group_affiliations: 1 resolvable (G1) + 1 dangling (MISSING)
    pa = by_edge["people.group_affiliations[].GroupID -> groups"]
    assert pa["total_refs"] == 2 and pa["dangling"] == 1
    # casualties.PersonID: c1 resolvable, c2 dangling
    cp = by_edge["casualties.PersonID -> people"]
    assert cp["dangling"] == 1
    # casualties.event_context.EventID resolves (EV1 present)
    ce = by_edge["casualties.event_context.EventID -> events"]
    assert ce["total_refs"] == 1 and ce["dangling"] == 0
    # weather.location.PlaceID dangling
    wp = by_edge["weather.location.PlaceID -> places"]
    assert wp["dangling"] == 1
    assert rep["total_dangling"] >= 3


def test_null_refs_are_not_dangling():
    st = FakeStorage(
        {
            "casualties/c.json": {"CasualtyID": P1, "type": "KIA", "PersonID": None},
            "people/a.json": {"PersonID": P1, "name": "A"},
        }
    )
    rep = ria.audit(st)
    cp = {e["edge"]: e for e in rep["edges"]}["casualties.PersonID -> people"]
    assert cp["total_refs"] == 0 and cp["dangling"] == 0  # null is allowed, not counted


def test_resolve_path_list_and_dotted():
    data = {"a": {"b": "x"}, "lst": [{"id": "1"}, {"id": "2"}, {"noid": "3"}]}
    assert ria._resolve_path(data, "a.b") == ["x"]
    assert ria._resolve_path(data, "lst[].id") == ["1", "2"]
