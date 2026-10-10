"""Tests for the corpus referential-integrity audit (read-only, offline)."""

import src.utils.referential_integrity as ria

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
        "people/a.json": {"PersonID": P1, "name": "A"},
        "people/b.json": {"PersonID": P2, "name": "B"},
        # groups (registry entity name is 'people_groups')
        "people_groups/g.json": {"GroupID": G1, "group_name": "1st Div"},
        # events (nested under content/)
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
        # source_section: one dangling EventID (the live write-time-dangling regression)
        "source_section/s.json": {"SourceSectionID": G1, "EventID": MISSING},
    }


def test_pk_sets_collects_pks_and_events():
    st = FakeStorage(_corpus())
    sets = ria.build_pk_sets(st)
    assert P1 in sets["people"] and P2 in sets["people"]
    assert G1 in sets["people_groups"]  # registry entity name
    assert EV1 in sets["events"] and P2 in sets["events"]  # EventID + Sub-eventID


def test_audit_counts_dangling_and_resolvable():
    st = FakeStorage(_corpus())
    rep = ria.audit(st)
    by_edge = {e["edge"]: e for e in rep["edges"]}
    # casualties.PersonID: c1 resolvable, c2 dangling
    cp = by_edge["casualties.PersonID -> people"]
    assert cp["dangling"] == 1
    # casualties.event_context.EventID resolves (EV1 present)
    ce = by_edge["casualties.event_context.EventID -> events"]
    assert ce["total_refs"] == 1 and ce["dangling"] == 0
    # source_section.EventID dangling (the regression this feature targets)
    ss = by_edge["source_section.EventID -> events"]
    assert ss["total_refs"] == 1 and ss["dangling"] == 1
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
    assert ria.resolve_path(data, "a.b") == ["x"]
    assert ria.resolve_path(data, "lst[].id") == ["1", "2"]


def test_edges_cover_every_schema_reference_field():
    """DRIFT GUARD: every PK-named reference field declared in any registry schema (that is not
    the entity's own PK) must appear as a derived edge. This is the machine-checkable guarantee
    that enforcement covers ALL json objects' reference fields, not a hand-maintained subset.
    """
    from src.schemas.entity_registry import ENTITY_REGISTRY, load_schema

    pk_to_entity = ria._pk_field_to_entity()
    covered = {(src, field) for src, field, _t, _n in ria.EDGES}
    missing = []
    for spec in ENTITY_REGISTRY:
        if spec.name == "events":
            continue
        try:
            schema = load_schema(spec)
        except Exception:  # noqa: BLE001
            continue
        for field_path in ria._walk_id_fields(schema):
            leaf = field_path.split(".")[-1].replace("[]", "")
            if leaf == spec.required_id:
                continue
            target = pk_to_entity.get(leaf)
            if not target or target == spec.name:
                continue
            if (spec.name, field_path) not in covered:
                missing.append(f"{spec.name}.{field_path} -> {target}")
    assert not missing, f"schema reference fields NOT covered by EDGES: {missing}"


def test_pk_targets_cover_every_registry_primary_key():
    """DRIFT GUARD: every registry entity with a primary key is a resolvable target."""
    from src.schemas.entity_registry import ENTITY_REGISTRY

    for spec in ENTITY_REGISTRY:
        if spec.required_id:
            assert (
                spec.name in ria.PK_FIELD
            ), f"{spec.name} PK not collected as a target"


def test_enforce_alerts_when_dangling(monkeypatch):
    """enforce() publishes a single SNS alert (aggregate only) when dangling refs exist."""
    published = {}

    class _SNS:
        def publish(self, **kw):
            published.update(kw)

    import src.utils.referential_integrity as mod

    class _Boto:
        def client(self, *_a, **_k):
            return _SNS()

    monkeypatch.setitem(__import__("sys").modules, "boto3", _Boto())
    st = FakeStorage(_corpus())
    rep = mod.enforce(st, alert_topic_arn="arn:aws:sns:us-east-1:1:topic")
    assert rep["total_dangling"] > 0
    assert "dangling cross-reference" in published["Message"]
    # Aggregate only — no raw record IDs leaked into the alert body.
    assert MISSING not in published["Message"]


def test_enforce_no_alert_when_clean(monkeypatch):
    """No topic configured -> enforce() still returns a report and never raises."""
    st = FakeStorage(_corpus())
    rep = ria.enforce(st, alert_topic_arn="")
    assert "total_dangling" in rep


def test_enforce_is_fail_safe_on_audit_error():
    """A broken storage must not crash the phase — enforce() swallows and reports."""

    class _Broken:
        def list_files(self, *_a, **_k):
            raise RuntimeError("boom")

        def read_json(self, *_a, **_k):
            raise RuntimeError("boom")

    rep = ria.enforce(_Broken())
    assert rep["total_dangling"] == 0
