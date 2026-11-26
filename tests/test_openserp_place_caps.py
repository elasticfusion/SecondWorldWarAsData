"""A place is capped like a person (owner 2026-11-23): at most ``max_web_results_per_entity``
new finds per search and ``max_images_per_entity`` images on the record; once a cap is reached
no further name variant or query is sent."""

import logging

import pytest

import src.enrichment.openserp_enrichment as oe


def _results(prefix, n=5):
    return [
        {
            "url": f"https://example.org/{prefix}{i}",
            "title": f"Metz {i}",
            "description": "d",
        }
        for i in range(n)
    ]


def _searches(monkeypatch):
    sent = []

    def search(q, url, **k):
        sent.append(q)
        return _results(f"{len(sent)}-")

    monkeypatch.setattr(oe, "_search_openserp", search)
    return sent


def test_new_finds_stop_at_the_cap_and_no_more_queries_are_sent(monkeypatch):
    sent = _searches(monkeypatch)
    monkeypatch.setattr(oe, "prefetch_relevance", lambda *a, **k: 0)
    monkeypatch.setattr(oe, "find_page", lambda *a, **k: {"url": a[0]})
    queued = oe._enrich_place_sources(
        {"PlaceID": "01HPL"},
        ["Metz", "Metz (Moselle)", "Mes"],
        "Metz",
        "u",
        object(),
        3,
    )
    assert queued is True and len(sent) == 1  # 3 of the first query's 5 results: done


def test_images_stop_at_the_cap(monkeypatch):
    from src.enrichment import web_photos

    sent = []

    def image_search(q, url, limit=None):
        sent.append(q)
        return [{"url": f"https://i.example/{len(sent)}-{n}.jpg"} for n in range(5)]

    monkeypatch.setattr(oe, "_search_openserp_images", image_search)
    monkeypatch.setattr(
        web_photos,
        "find_photos",
        lambda results, subject, grok, existing, wanted, **k: [
            {"url": r["url"]} for r in results[:wanted]
        ],
    )
    data = {"PlaceID": "01HPL"}
    assert oe._enrich_place_images(data, ["Metz", "Mes"], "Metz", "u", object(), 3)
    assert (
        len(data["images"]) == 3 and len(sent) == 1
    )  # no second query, no second name


def test_a_place_with_its_images_is_not_image_searched(monkeypatch):
    sent = _searches(monkeypatch)
    data = {
        "PlaceID": "01HPL",
        "images": [{"url": f"https://e.org/{i}"} for i in range(3)],
    }
    monkeypatch.setattr(oe, "_search_openserp_images", lambda *a, **k: sent.append(a))
    assert oe._enrich_place_images(data, ["Metz"], "Metz", "u", object(), 3) is False
    assert sent == []


def test_the_caps_come_from_config(monkeypatch):
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {
            "openserp": {"max_images_per_entity": 2, "max_web_results_per_entity": 4}
        },
    )
    assert oe._entity_caps() == (2, 4)
    assert oe._entity_caps(7, None) == (7, 4)  # an explicit cap wins


def test_enrich_one_place_passes_its_caps(monkeypatch):
    seen = {}
    monkeypatch.setattr(oe, "_entity_caps", lambda i, w: (2, 4))
    monkeypatch.setattr(
        oe, "_enrich_place_images", lambda *a: seen.update(images=a[-1]) or False
    )
    monkeypatch.setattr(
        oe, "_enrich_place_sources", lambda *a: seen.update(web=a[-1]) or False
    )
    oe.enrich_one_place({"current_name": "Metz", "PlaceID": "01H"}, "u", object())
    assert seen == {"images": 2, "web": 4}


def test_the_search_moves_on_to_the_next_name_until_the_cap(monkeypatch):
    sent = _searches(monkeypatch)
    monkeypatch.setattr(oe, "prefetch_relevance", lambda *a, **k: 0)
    queued = iter([None] * 5 + [{"u": 1}, {"u": 2}] + [None] * 30)  # 2 finds in query 2
    monkeypatch.setattr(oe, "find_page", lambda *a, **k: next(queued))
    assert oe._enrich_place_sources(
        {"PlaceID": "01HPL"}, ["Metz", "Mes"], "Metz", "u", object(), 3
    )
    assert len(sent) == 4  # every query of both names: the cap of 3 was never reached


@pytest.mark.parametrize("bad", [0, -2, "three", None, 2.5])
def test_a_bad_cap_is_said_and_the_default_used(monkeypatch, caplog, bad):
    from src.utils import observe

    monkeypatch.setattr(observe, "_counts", {})
    monkeypatch.setattr(
        "src.utils.config.load_config",
        lambda *a, **k: {"openserp": {"max_web_results_per_entity": bad}},
    )
    with caplog.at_level(logging.WARNING):
        assert oe._entity_caps() == (1, 5)
    assert "bad_max_web_results_per_entity" in caplog.text
