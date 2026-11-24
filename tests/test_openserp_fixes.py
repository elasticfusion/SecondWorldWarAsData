"""Tests for the OpenSERP fixes: templated queries, append-dedup, write-time validation."""

from unittest.mock import patch

import src.enrichment.openserp_enrichment as oe


def test_equipment_images_use_yaml_templates():
    # Captures the queries sent to _search_openserp — they must come from equipment.yaml,
    # not a hardcoded f-string.
    seen = []

    def fake_search(q, url, limit=None):
        seen.append(q)
        return []

    with patch.object(oe, "_search_openserp_images", side_effect=fake_search):
        oe.search_equipment_images(
            "M4 Sherman",
            "http://x",
            identifier="M4",
            year="1944",
            equipment_id="01HEQUIPMENT0000000000000",
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


def test_person_photos_fill_only_the_room_left_and_skip_what_is_held():
    data = {"PersonID": "01HJOHN", "images": [{"url": "http://dup", "source": "x"}]}
    seen = {}

    def fake(subject, queries, url, grok, existing, wanted):
        seen.update(
            wanted=wanted, existing=[i["url"] for i in existing], key=subject.entity_key
        )
        return [{"url": "http://new", "vision_verified": True}]

    with patch.object(oe, "find_entity_photos", side_effect=fake):
        changed = oe._verify_and_apply(
            {"openserp_url": "http://serp", "web_results": []},
            data,
            "John Smith",
            object(),
            max_images=3,
            max_web=5,
        )
    assert changed and [i["url"] for i in data["images"]] == [
        "http://dup",
        "http://new",
    ]
    assert seen == {"wanted": 2, "existing": ["http://dup"], "key": "person#01HJOHN"}


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


def test_search_timeout_is_config_driven():
    """The OpenSERP per-query HTTP timeout comes from config (raised for browser latency),
    not a hardcoded 30s. Defaults to 60 when unset."""
    from unittest.mock import MagicMock

    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"results": [{"url": "u", "title": "t", "snippet": "s"}]}

    def fake_request(method, url, params=None, timeout=None, **k):
        captured["timeout"] = timeout
        return _Resp()

    sess = MagicMock()
    sess.request.side_effect = fake_request

    # Explicit config value is honored.
    with (
        patch.object(oe, "_breaker_is_open", lambda: False),
        patch("src.utils.http_pool.get_session", lambda: sess),
        patch.object(oe, "_breaker_record_success", lambda: None),
        patch(
            "src.utils.config.load_config",
            lambda: {
                "openserp": {"rate_limit_seconds": 0, "request_timeout_seconds": 90}
            },
        ),
    ):
        oe._search_openserp("q", "http://x", limit=5)
    assert captured["timeout"] == 90

    # Default when the key is absent is 60 (not the old hardcoded 30).
    with (
        patch.object(oe, "_breaker_is_open", lambda: False),
        patch("src.utils.http_pool.get_session", lambda: sess),
        patch.object(oe, "_breaker_record_success", lambda: None),
        patch(
            "src.utils.config.load_config",
            lambda: {"openserp": {"rate_limit_seconds": 0}},
        ),
    ):
        oe._search_openserp("q2", "http://x", limit=5)  # "q" is answered from the cache
    assert captured["timeout"] == 60
