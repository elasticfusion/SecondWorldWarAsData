"""Tests for the OpenSERP fixes: templated queries, append-dedup, write-time validation."""

from unittest.mock import patch

import src.enrichment.openserp_enrichment as oe


def test_equipment_images_use_yaml_templates():
    # Captures the queries sent to _search_openserp — they must come from equipment.yaml,
    # not a hardcoded f-string.
    seen = []

    def fake_search(q, url, limit=10):
        seen.append(q)
        return []

    with patch.object(oe, "_search_openserp", side_effect=fake_search):
        oe.search_equipment_images(
            "M4 Sherman", "http://x", identifier="M4", year="1944"
        )
    assert seen  # queries were rendered + issued
    # equipment.yaml 'images' templates quote the name and include identifier/year variants
    assert any("M4 Sherman" in q for q in seen)
    assert any("1944" in q for q in seen)


def test_awards_use_yaml_templates():
    seen = []

    def fake_search(q, url, limit=10):
        seen.append(q)
        return []

    with (
        patch.object(oe, "_search_openserp", side_effect=fake_search),
        patch("src.utils.search_cache.get_cached", return_value=None),
        patch("src.utils.search_cache.cache_result"),
    ):
        oe.search_military_awards("John Smith", "http://x")
    assert seen
    # people.yaml 'web_results' templates render the name
    assert all("John Smith" in q for q in seen)


def test_verify_and_apply_dedupes_existing_urls():
    data = {"images": [{"url": "http://dup", "source": "x"}], "military_awards": []}
    cand = {
        "image_results": [
            {"url": "http://dup", "title": "John Smith"},
            {"url": "http://new", "title": "John Smith"},
        ],
        "web_results": [],
    }
    with patch.object(oe, "_verify_result", return_value=True):
        changed = oe._verify_and_apply(
            cand, data, "John Smith", object(), max_images=5, max_web=5
        )
    urls = [i["url"] for i in data["images"]]
    assert changed
    assert urls.count("http://dup") == 1  # not duplicated
    assert "http://new" in urls


def test_validate_before_write_rejects_bad_allows_good():
    assert (
        oe._validate_before_write({"PersonID": "bad", "images": []}, "people") is False
    )
    assert (
        oe._validate_before_write(
            {"PersonID": "01HX7YZABCDEFGHJKMNPQRSTVW", "name": "X"}, "people"
        )
        is True
    )


def test_validate_unknown_entity_allows_write():
    # fail-safe: unknown entity -> don't block the write
    assert oe._validate_before_write({"x": 1}, "not_an_entity") is True
