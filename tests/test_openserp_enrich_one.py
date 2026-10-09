"""Tests for the per-entity ``enrich_one_<type>`` wrappers (SQS worker unit of work).

Each wrapper must:
  * delegate to the SAME search helper the batch driver uses (no duplicated search logic),
  * mutate ``data`` in place and return ``changed`` (bool),
  * NOT touch the filesystem or set the ``openserp_searched`` marker (the caller owns that).

All Grok + OpenSERP calls are mocked — no live network.
"""

from unittest.mock import patch

import src.enrichment.openserp_enrichment as oe

# --- enrich_one_equipment ---------------------------------------------------------------


def test_enrich_one_equipment_delegates_and_applies():
    data = {"common_name": "M4 Sherman", "technical_identifier": "M4"}
    with patch.object(
        oe,
        "search_equipment_images",
        return_value=[
            {"url": "http://img/1", "title": "Sherman", "source": "openserp"}
        ],
    ) as mock_search:
        changed = oe.enrich_one_equipment(data, "http://x", grok_client=object())
    assert changed is True
    assert mock_search.called
    # delegated with the equipment name as the first positional arg
    assert mock_search.call_args.args[0] == "M4 Sherman"
    assert data["images"][0]["url"] == "http://img/1"
    # no persistence side effects
    assert "openserp_searched" not in data


def test_enrich_one_equipment_no_results_returns_false():
    data = {"common_name": "Unknown Thing"}
    with patch.object(oe, "search_equipment_images", return_value=[]):
        changed = oe.enrich_one_equipment(data, "http://x", grok_client=object())
    assert changed is False
    assert "images" not in data


def test_enrich_one_equipment_skips_when_already_has_images():
    data = {"common_name": "M4", "images": [{"url": "http://existing"}]}
    with patch.object(oe, "search_equipment_images") as mock_search:
        changed = oe.enrich_one_equipment(data, "http://x", grok_client=object())
    assert changed is False
    assert not mock_search.called  # must NOT re-search when an image already exists


def test_enrich_one_equipment_no_name_returns_false():
    assert oe.enrich_one_equipment({}, "http://x", grok_client=object()) is False


# --- enrich_one_source_section ----------------------------------------------------------


def test_enrich_one_source_section_delegates_and_applies():
    data = {"operation": {"name": "Battle of the Bulge", "aliases": ["Ardennes"]}}
    with patch.object(
        oe,
        "search_event_content",
        return_value=[{"url": "http://s1", "title": "AAR", "source": "openserp"}],
    ) as mock_search:
        changed = oe.enrich_one_source_section(data, "http://x", grok_client=object())
    assert changed is True
    assert mock_search.call_args.args[0] == "Battle of the Bulge"
    assert data["primary_sources"][0]["url"] == "http://s1"
    assert "openserp_searched" not in data


def test_enrich_one_source_section_no_operation_returns_false():
    with patch.object(oe, "search_event_content") as mock_search:
        changed = oe.enrich_one_source_section(
            {"operation": None}, "http://x", grok_client=object()
        )
    assert changed is False
    assert not mock_search.called


def test_enrich_one_source_section_dedupes():
    data = {
        "operation": {"name": "Battle of Metz"},
        "primary_sources": [{"url": "http://dup", "source": "openserp"}],
    }
    with patch.object(
        oe,
        "search_event_content",
        return_value=[
            {"url": "http://dup", "title": "x"},
            {"url": "http://new", "title": "y"},
        ],
    ):
        changed = oe.enrich_one_source_section(data, "http://x", grok_client=object())
    assert changed is True
    urls = [s["url"] for s in data["primary_sources"]]
    assert urls.count("http://dup") == 1 and "http://new" in urls


# --- enrich_one_group -------------------------------------------------------------------


def test_enrich_one_group_delegates_per_category():
    data = {"group_name": "101st Airborne", "nationality": "American"}
    calls = []

    def fake_cat(name, nat, category, url, grok, max_results):
        calls.append(category)
        return [{"url": f"http://{category}", "title": name, "source": "openserp"}]

    with patch.object(oe, "_group_openserp_category", side_effect=fake_cat):
        changed = oe.enrich_one_group(data, "http://x", grok_client=object())
    assert changed is True
    # delegated to the shared category helper for all three categories
    assert calls == ["images", "web_results", "veterans_association"]
    assert data["images"] and data["web_results"] and data["veterans_associations"]
    assert "openserp_searched" not in data


def test_enrich_one_group_no_results_returns_false():
    data = {"group_name": "7th Army", "nationality": "German"}
    with patch.object(oe, "_group_openserp_category", return_value=[]):
        changed = oe.enrich_one_group(data, "http://x", grok_client=object())
    assert changed is False


def test_enrich_one_group_short_name_returns_false():
    with patch.object(oe, "_group_openserp_category") as mock_cat:
        changed = oe.enrich_one_group(
            {"group_name": "A"}, "http://x", grok_client=object()
        )
    assert changed is False
    assert not mock_cat.called


# --- enrich_one_place -------------------------------------------------------------------


def test_enrich_one_place_delegates_images_and_sources():
    data = {
        "current_name": "Palatinate",
        "historical_names": [{"name": "Pfalz", "language": "German"}],
    }
    seen = []

    def fake_search(q, url, limit=10):
        seen.append(q)
        return [
            {"url": f"http://{abs(hash(q)) % 1000}", "title": "x WWII", "snippet": ""}
        ]

    with (
        patch.object(oe, "_search_openserp", side_effect=fake_search),
        patch.object(oe, "_verify_result", return_value=True),
        patch.object(
            oe,
            "process_positive_url",
            return_value={"url": "http://s", "title": "t", "summary": "sum"},
        ),
    ):
        changed = oe.enrich_one_place(data, "http://x", grok_client=object())
    assert changed is True
    # searched both the English and the native German WWII name variant
    assert any("Palatinate" in q for q in seen)
    assert any("Pfalz" in q for q in seen)
    assert data["images"] and data["web_results"]
    assert "openserp_searched" not in data


def test_enrich_one_place_no_variants_returns_false():
    with patch.object(oe, "_search_openserp") as mock_search:
        changed = oe.enrich_one_place({}, "http://x", grok_client=object())
    assert changed is False
    assert not mock_search.called


def test_enrich_one_place_no_hits_returns_false():
    data = {"current_name": "Nowhere"}
    with (
        patch.object(oe, "_search_openserp", return_value=[]),
        patch.object(oe, "_verify_result", return_value=False),
    ):
        changed = oe.enrich_one_place(data, "http://x", grok_client=object())
    assert changed is False
