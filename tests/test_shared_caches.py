"""Task 1.2f: state that used to live only on the task's disk (lost with every task, a Spot
reclaim included) is kept in the shared search cache (DynamoDB in AWS): Nominatim answers and
the OpenSERP map-search history. An outage is never stored as an answer; a map search is
marked only once its results were processed."""

from unittest import mock

from src.enrichment import nominatim_geocode as ng
from src.enrichment.places_grok_geocode import GeocodeResult


def _geocoder(tmp_path, monkeypatch, answers):
    calls = []

    def query(q, session, cc):
        calls.append(q)
        out = answers.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(ng, "_query_nominatim", query)
    return ng.make_nominatim_geocoder(tmp_path, session=object()), calls


def test_a_nominatim_answer_survives_the_task(tmp_path, monkeypatch):
    hit = [{"lat": "50.77", "lon": "6.08", "importance": 0.9, "display_name": "Aachen"}]
    first, calls = _geocoder(tmp_path / "task1", monkeypatch, [hit])
    a = first("Aachen", {})
    # a new task: an empty disk, the same shared cache
    second, calls2 = _geocoder(tmp_path / "task2", monkeypatch, [])
    b = second("Aachen", {})
    assert calls == ["Aachen"] and calls2 == []
    assert (a.latitude, a.longitude) == (b.latitude, b.longitude)


def test_a_nominatim_outage_is_never_shared(tmp_path, monkeypatch):
    first, _ = _geocoder(tmp_path / "t1", monkeypatch, [RuntimeError("down")])
    assert first("Metz", {}).transient
    second, calls = _geocoder(tmp_path / "t2", monkeypatch, [[]])
    second("Metz", {})
    assert calls == ["Metz"]  # asked again: the outage was not stored


def test_a_shared_entry_cannot_claim_to_be_an_outage(monkeypatch):
    from src.utils import search_cache

    search_cache._get_backend().put(
        ng._shared_key("X|"),
        GeocodeResult(found=False, transient=True).model_dump_json(),
    )
    assert ng._shared_get("X|").transient is False


def test_the_map_search_history_survives_the_task(tmp_path):
    from src.extraction.search_history import SearchHistory

    SearchHistory(tmp_path / "a.json").mark_searched("Aachen")
    assert SearchHistory(tmp_path / "b.json").has_searched("AACHEN")


def test_a_map_search_is_marked_only_after_its_results(tmp_path, monkeypatch):
    import json

    from src.extraction import openserp_maps as om

    place = tmp_path / "p.json"
    place.write_text(json.dumps({"current_name": "Aachen"}))
    history = mock.Mock()
    history.has_searched.return_value = False
    order = []
    history.mark_searched.side_effect = lambda n: order.append("marked")
    monkeypatch.setattr(om, "search_with_openserp", lambda *a, **k: [{"url": "u"}])
    monkeypatch.setattr(
        om,
        "_process_search_result",
        lambda *a, **k: order.append("processed") or (False, 0),
    )
    om._process_place_file(
        place,
        1,
        1,
        history,
        True,
        5,
        "http://s",
        mock.Mock(),
        1,
        1,
        None,
        tmp_path,
        set(),
    )
    assert order == ["processed", "marked"]


def test_implausible_shared_coordinates_are_not_trusted():
    from src.utils import search_cache

    bad = GeocodeResult(found=True, latitude=123.0, longitude=6.0, confidence=0.9)
    search_cache._get_backend().put(ng._shared_key("Y|"), bad.model_dump_json())
    assert ng._shared_get("Y|") is None  # asked again, never used as coordinates


def test_slack_markup_in_an_alert_is_shown_not_acted_on():
    from lambda_handlers import slack_formatter as sf

    text = sf._description(
        "Alert", "re-upload s3://b/contentrepository/<!channel> & <https://x|y>"
    )
    assert "<!channel>" not in text and "&lt;!channel&gt;" in text and "&amp;" in text


def test_an_alert_note_keeps_its_spaces_so_a_command_can_be_pasted(caplog):
    from src.ingestion.ocr_chunk_manifest import redrive_command
    from src.utils.observe import observe_warn

    cmd = redrive_command("Some_Book")
    observe_warn("ocr_merge", "x", note=f"re-drive: {cmd}\nOBSERV forged status=ok")
    line = [
        r.getMessage() for r in caplog.records if "OBSERV ocr_merge.x" in r.getMessage()
    ][0]
    assert cmd in line  # pasteable as written
    assert "\n" not in line  # still one line: nothing can be forged


def test_a_note_cannot_pose_as_another_observ_record(caplog):
    from src.utils.observe import observe_warn

    observe_warn("t", "e", note="see OBSERV autoscale.x status=ok")
    line = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("OBSERV t.e")
    ][0]
    assert line.count("OBSERV ") == 1
