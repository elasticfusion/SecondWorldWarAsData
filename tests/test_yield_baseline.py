"""Deterministic expected-yield baseline + severity classification."""

from src.utils import yield_baseline as YB


def test_groups_unit_designators():
    txt = "The 101st Airborne Division and 82d Airborne Division landed. Force U and Force O. The RAF flew. Fifteenth Army... no wait, 15th Army and VII Corps."
    n = YB.deterministic_count("groups", txt)
    # 101st Division, 82d Division, Force U, Force O, RAF, 15th Army (VII Corps is roman, not matched by \d regex)
    assert n >= 5, n


def test_people_clusters_collapse_aliases():
    txt = "General Cota advised. Later Norman Cota returned. Brig. Gen. Cota was wounded. Meanwhile General Roosevelt landed."
    # Cota (3 mentions) -> 1 cluster by surname; Roosevelt -> 1. Total 2.
    assert YB.deterministic_count("people", txt) == 2


def test_equipment_dict_terms():
    txt = "DD tanks and howitzers supported the glider landings against E-boats."
    terms = ["tank", "howitzer", "glider", "e-boat", "destroyer"]
    assert YB.deterministic_count("equipment", txt, terms) == 4  # destroyer absent


def test_classify_entity_below_min_baseline_is_ok():
    assert YB.classify_entity(deterministic=3, produced=0) == "ok"  # floor too small


def test_classify_entity_starved():
    assert YB.classify_entity(deterministic=25, produced=0) == "starved"


def test_classify_entity_warn():
    assert YB.classify_entity(deterministic=25, produced=5) == "warn"  # 0.2 < 0.5


def test_classify_entity_ok_healthy():
    assert YB.classify_entity(deterministic=25, produced=24) == "ok"


def test_classify_run_fail_stop_on_single_starved():
    v, off = YB.classify_run({"equipment": (8, 0), "places": (12, 10)})
    assert v == "fail_stop" and "equipment" in off


def test_classify_run_fail_stop_on_multi_entity_short():
    v, off = YB.classify_run({"groups": (74, 6), "people": (82, 1), "places": (12, 11)})
    assert v == "fail_stop"
    assert "groups" in off and "people" in off


def test_classify_run_warn_on_single_moderate():
    v, off = YB.classify_run({"groups": (25, 10), "places": (12, 12)})
    assert v == "warn" and off == ["groups"]


def test_classify_run_ok_when_healthy():
    v, off = YB.classify_run(
        {"groups": (25, 24), "places": (12, 12), "people": (30, 28)}
    )
    assert v == "ok" and off == []


def test_run_quality_gate_fail_stop_and_metrics(tmp_path):
    import json
    from src.utils import yield_baseline as YB

    # build a tiny corpus with MANY group designators but produced counts of ~0
    cdir = tmp_path / "content" / "Book"
    cdir.mkdir(parents=True)
    text = " ".join(
        f"{n}st Division advanced." for n in range(1, 30)
    )  # ~29 unit designators
    (cdir / "chapter1full-parsed.json").write_text(
        json.dumps({"paragraphs": [{"text": text, "absolute_number": 1}]})
    )
    verdict, offenders = YB.run_quality_gate(
        tmp_path, {"groups": 0, "people": 0, "equipment": 0, "places": 0}
    )
    assert verdict == "fail_stop", (verdict, offenders)
    assert "groups" in offenders
    # cumulative metrics written
    mf = tmp_path / "metrics" / "yield_quality.json"
    assert mf.exists()
    hist = json.loads(mf.read_text())
    assert hist[-1]["verdict"] == "fail_stop"
    assert hist[-1]["entities"]["groups"]["deterministic"] >= 20


def test_run_quality_gate_ok_on_empty_text(tmp_path):
    from src.utils import yield_baseline as YB

    (tmp_path / "content").mkdir(parents=True)
    verdict, offenders = YB.run_quality_gate(tmp_path, {})
    assert verdict == "ok" and offenders == []
