"""Corpus-frequency surname SUGGESTION report: skew guard, growth trigger, suggestion-only."""

import json

from src.dedup import surname_report as sr


def _person(pid, name, nat="USA"):
    return {"PersonID": pid, "name": name, "biographical_profile": {"nationality": nat}}


def test_skew_guard_mentions_do_not_make_common():
    # One Patton, however many mentions -> rare (distinct person count, not mentions)
    rep = sr.build_suggestions([_person("P1", "George S. Patton")], {})
    pat = [s for s in rep["suggestions"] if s["surname"] == "patton"][0]
    assert pat["distinct_people"] == 1
    assert pat["observed_band"] == "rare"


def test_many_distinct_people_flagged_common():
    firsts = [
        "John",
        "James",
        "Robert",
        "William",
        "David",
        "Richard",
        "Thomas",
        "Charles",
        "Joseph",
        "Paul",
        "Mark",
        "Steven",
    ]
    smiths = [_person(f"S{i}", f"{fn} Smith") for i, fn in enumerate(firsts)]
    rep = sr.build_suggestions(smiths, {})
    sm = [s for s in rep["suggestions"] if s["surname"] == "smith"][0]
    assert sm["distinct_people"] == 12
    assert sm["observed_band"] == "very_common"


def test_no_suggestion_when_curated_matches():
    rep = sr.build_suggestions([_person("P1", "John Rare")], {"USA": {"rare": "rare"}})
    assert all(s["surname"] != "rare" for s in rep["suggestions"])


def test_nationality_conditioned_and_unknown_skipped():
    # unknown nationality -> not counted (commonness is nationality-conditioned)
    rep = sr.build_suggestions(
        [{"PersonID": "X", "name": "Jan Kowalski", "biographical_profile": {}}], {}
    )
    assert rep["suggestion_count"] == 0


def test_growth_trigger_gates_regeneration(tmp_path):
    pdir = tmp_path
    # 3 people, min_new_people high -> below threshold -> no report
    for i in range(3):
        (pdir / f"p{i}.json").write_text(
            json.dumps(_person(f"P{i}", f"Name{i} Smith")), encoding="utf-8"
        )
    cfg = {"dedup": {"people": {"surname_report": {"min_new_people": 50}}}}
    assert sr.maybe_generate(pdir, cfg) is None
    assert not (pdir / sr.SUGGESTIONS_FILENAME).exists()

    # min_new_people low -> growth met -> report generated (suggestion-only)
    cfg2 = {"dedup": {"people": {"surname_report": {"min_new_people": 1}}}}
    out = sr.maybe_generate(pdir, cfg2)
    assert out is not None and out.exists()
    data = json.loads(out.read_text())
    assert "suggestions" in data and "SUGGESTIONS ONLY" in data["_note"]

    # re-run without growth -> gated (state recorded)
    assert sr.maybe_generate(pdir, cfg2) is None


def test_report_never_writes_curated_table(tmp_path, monkeypatch):
    pdir = tmp_path
    (pdir / "p0.json").write_text(
        json.dumps(_person("P0", "A Smith")), encoding="utf-8"
    )
    # Point the curated table path at a sentinel and assert it is never modified.
    sentinel = tmp_path / "curated.yaml"
    sentinel.write_text("surnames: {}\n", encoding="utf-8")
    before = sentinel.read_text()
    cfg = {
        "dedup": {
            "people": {
                "surname_report": {"min_new_people": 1},
                "surname_frequency": {"path": str(sentinel)},
            }
        }
    }
    sr.maybe_generate(pdir, cfg)
    assert sentinel.read_text() == before  # curated table untouched
