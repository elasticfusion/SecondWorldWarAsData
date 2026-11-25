"""What a map shows, kept on its Map record (map_depicted): cleaned model output, provenance, the
legibility gate (M7) and the never-ask-twice marker."""

import pytest

from src.extraction import map_depicted as md


def test_only_known_fields_are_kept_and_capped():
    out = md.clean(
        {
            "units": ["28th Infantry Division", "", 5, "x" * 900],
            "places": "Schmidt",  # not a list: dropped
            "description": "  Tactical map near Schmidt.  ",
            "legibility_confidence": "0.8",
            "script": "<script>",
        }
    )
    assert out == {
        "units": ["28th Infantry Division", "x" * 500],
        "description": "Tactical map near Schmidt.",
        "legibility_confidence": 0.8,
    }


def test_nothing_usable_is_none():
    assert (
        md.clean({}) is None
        and md.clean("x") is None
        and md.clean({"units": []}) is None
    )


def test_the_legibility_gate():
    assert md.is_legible({"legibility_confidence": 0.85})
    assert md.is_legible({"legibility_confidence": md.MIN_LEGIBILITY_CONFIDENCE})
    assert not md.is_legible({"legibility_confidence": 0.15})
    assert md.is_legible({})  # no stated confidence: not gated
    assert md.is_legible({"legibility_confidence": "high"})  # unparseable: not gated
    assert md.legibility_confidence({"legibility_confidence": "0.4"}) == 0.4


def test_apply_and_was_read():
    rec = {"MapID": "01HX7YZABCDEFGHJKMNPQRSTVW"}
    assert not md.was_read(rec)
    prov = md.provenance(
        md.NOT_RELEVANT,
        kind="k",
        model=None,
        request_id=None,
        image_url="u",
        found_in="b",
    )
    md.apply(rec, {"units": ["x"]}, prov)
    assert rec["depicted"] is None and md.was_read(rec)  # not relevant: nothing kept


def test_the_request_id_is_the_one_the_client_stamps(monkeypatch):
    """The answer comes back without the echoed id (the client checks and removes it), so the
    recorded id is the one the client asks with: the same id its identity block carries.
    """
    from src.grok_client import GrokClient
    from src.utils import request_identity

    client = GrokClient.__new__(
        GrokClient
    )  # no network, no config: only the id derivation
    rid = md.request_id_for(
        client, "Is this a map?", "https://x.org/m.png", "external_maps_verification"
    )
    assert isinstance(rid, str) and len(rid) == 16
    stamped, _, stamped_rid = request_identity.stamp(
        "Is this a map?",
        None,
        "external_maps_verification",
        None,
        "Is this a map?:https://x.org/m.png",
    )
    assert rid == stamped_rid and f'"RequestID": "{rid}"' in stamped


def test_no_request_id_when_the_client_cannot_say():
    class Odd:
        def vision_request_id(self, *a):
            raise RuntimeError("x")

    assert md.request_id_for(Odd(), "p", "u", "k") is None
    assert md.request_id_for(object(), "p", "u", "k") is None


def test_openserp_maps_keep_what_they_show_and_no_longer_recycle():
    import inspect

    import src.extraction.openserp_maps as om

    src = inspect.getsource(om._process_search_result)
    assert "map_depicted.apply" in src and "recycle_map_as_source" not in src


def test_a_confidence_that_is_not_a_finite_number_is_not_kept():
    for raw in (float("nan"), "1e999", True, "x"):
        assert md.legibility_confidence({"legibility_confidence": raw}) is None
    assert md.legibility_confidence({"legibility_confidence": 7}) == 1.0  # clamped
    assert md.legibility_confidence({"legibility_confidence": -1}) == 0.0


def test_the_verdict_follows_relevance_and_usable_contents():
    assert (
        md.verdict_for({"is_relevant": True, "depicted": {"units": ["x"]}})
        == md.DEPICTED
    )
    assert (
        md.verdict_for({"is_relevant": False, "depicted": {"units": ["x"]}})
        == md.NOT_RELEVANT
    )
    assert md.verdict_for({"is_relevant": True, "depicted": {}}) == md.NOT_RELEVANT
    assert md.verdict_for(None) == md.NOT_RELEVANT


def test_a_malformed_index_entry_is_not_a_record(tmp_path):
    from src.extraction import map_link_source as mls

    (tmp_path / "index.json").write_text("{}")
    for name in (5, "index.json", "../x.json", "a/b.json", "m.txt"):
        assert mls._map_file(tmp_path, {"u": name}, {"url": "u"}) is None
    assert mls._map_file(tmp_path, ["not a dict"], {"url": "u"}) is None  # type: ignore[arg-type]


def test_an_in_doc_map_read_records_its_request_id(monkeypatch):
    """read_in_doc_maps stores the RequestID the vision request was asked with (it used to read
    it from the answer, which the client had already removed it from, so it was always null).
    """
    from pathlib import Path

    import src.extraction.map_link_source as mls

    stored = {}
    monkeypatch.setattr(mls, "_ssrf_safe_image_url", lambda u: u)
    monkeypatch.setattr(mls, "_map_file", lambda *a: Path("/m.json"))
    monkeypatch.setattr(mls, "_read_record", lambda f: {})
    monkeypatch.setattr(
        mls, "_vision_depicted", lambda *a, **k: {"is_map": True, "depicted": {}}
    )
    monkeypatch.setattr(
        md, "store", lambda f, depicted, prov: stored.update(prov) or True
    )

    class Client:
        def vision_request_id(self, prompt, image_url, kind):
            assert image_url.endswith("m1.jpg") and kind == mls.VISION_KIND
            return "0123456789abcdef"

        def _get_model(self, kind):
            return "grok-x"

    maps = [{"url": "https://www.ibiblio.org/hyperwar/x/maps/m1.jpg", "map_id": "1"}]
    out = mls.read_in_doc_maps([{"book": "B", "maps": maps}], Client(), Path("/n"))
    assert out["read"] == 1
    assert stored["request_id"] == "0123456789abcdef"


@pytest.mark.parametrize(
    "kind,book",
    [("in_doc_map_vision", "USA-E-XChannel"), ("external_maps_verification", None)],
)
def test_the_recorded_id_is_the_one_the_vision_call_stamps(monkeypatch, kind, book):
    """Drive the real _vision_json up to the API call and compare the RequestID in the prompt it
    sends with vision_request_id, in the same book scope (the document-bound case adds the book).
    """
    from src import grok_client as G

    client = G.GrokClient.__new__(G.GrokClient)
    client.batch_mode = False
    sent = {}
    monkeypatch.setattr(client, "_get_cache", lambda kind: {}, raising=False)
    monkeypatch.setattr(client, "_get_model", lambda kind: "m", raising=False)
    monkeypatch.setattr(
        client,
        "_build_vision_messages",
        lambda asked, data, sp: sent.setdefault("prompt", asked),
        raising=False,
    )

    def stop(*a, **k):
        raise RuntimeError("stop before the network")

    monkeypatch.setattr(client, "_call_api", stop, raising=False)
    url = "https://www.ibiblio.org/hyperwar/x/maps/m.jpg"
    with G.book_scope(book):
        with pytest.raises(RuntimeError):
            client._vision_json(
                "Is it a map?",
                f"Is it a map?:{url}",
                lambda: "data:",
                None,
                0.1,
                False,
                kind,
            )
        rid = client.vision_request_id("Is it a map?", url, kind)
    assert rid and f'"RequestID": "{rid}"' in sent["prompt"]
