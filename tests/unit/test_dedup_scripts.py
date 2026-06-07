"""Tests for src/dedup/incremental.py and find_duplicate_* scripts."""

# pylint: disable=missing-function-docstring

import time


class TestIncremental:
    """Tests for src/dedup/incremental."""

    def test_get_new_files_returns_modified(self, tmp_path):
        from src.dedup.incremental import get_new_files

        old_time = time.time() - 100
        # Create files with different mtimes
        (tmp_path / "old.json").write_text("{}", encoding="utf-8")
        (tmp_path / "new.json").write_text("{}", encoding="utf-8")
        # Set old file to past
        import os

        os.utime(tmp_path / "old.json", (old_time, old_time))

        result = get_new_files(tmp_path, old_time + 50)
        assert "new.json" in result
        assert "old.json" not in result

    def test_get_new_files_skips_index(self, tmp_path):
        from src.dedup.incremental import get_new_files

        (tmp_path / "index.json").write_text("{}", encoding="utf-8")
        (tmp_path / "duplicate_report.json").write_text("{}", encoding="utf-8")
        (tmp_path / "real.json").write_text("{}", encoding="utf-8")

        result = get_new_files(tmp_path, 0)
        assert "real.json" in result
        assert "index.json" not in result
        assert "duplicate_report.json" not in result

    def test_get_new_files_none_since_returns_empty(self, tmp_path):
        from src.dedup.incremental import get_new_files

        (tmp_path / "a.json").write_text("{}", encoding="utf-8")
        result = get_new_files(tmp_path, None)
        assert result == set()

    def test_should_compare_full_mode(self):
        from src.dedup.incremental import should_compare

        assert should_compare("a.json", "b.json", set()) is True

    def test_should_compare_incremental_one_new(self):
        from src.dedup.incremental import should_compare

        new_files = {"a.json"}
        assert should_compare("a.json", "b.json", new_files) is True
        assert should_compare("b.json", "a.json", new_files) is True

    def test_should_compare_incremental_neither_new(self):
        from src.dedup.incremental import should_compare

        new_files = {"c.json"}
        assert should_compare("a.json", "b.json", new_files) is False


class TestFindDuplicateEquipment:
    """Tests for scripts/find_duplicate_equipment.py helpers."""

    def test_normalize_caliber(self):
        from scripts.find_duplicate_equipment import _normalize

        assert "50 cal" in _normalize("50-caliber machine gun")
        assert "50 cal" in _normalize(".50-caliber")

    def test_normalize_mm(self):
        from scripts.find_duplicate_equipment import _normalize

        assert "155mm" in _normalize("155-mm howitzer")
        assert "155mm" in _normalize("155 mm")

    def test_similarity_identical(self):
        from scripts.find_duplicate_equipment import _similarity

        assert _similarity("Sherman Tank", "Sherman Tank") == 1.0

    def test_similarity_partial(self):
        from scripts.find_duplicate_equipment import _similarity

        score = _similarity("M4 Sherman", "M4A1 Sherman")
        assert score > 0.7

    def test_same_category(self):
        from scripts.find_duplicate_equipment import _same_category

        assert _same_category({"category": "Tank"}, {"category": "tank"}) is True
        assert _same_category({"category": "Tank"}, {"category": "Gun"}) is False
        assert _same_category({"category": ""}, {"category": "Tank"}) is False

    def test_score_pair_rejects_different_numbers(self):
        from scripts.find_duplicate_equipment import _score_pair

        item1 = {"common_name": "105mm Howitzer", "country_of_origin": "USA"}
        item2 = {"common_name": "155mm Howitzer", "country_of_origin": "USA"}
        conf, _, _ = _score_pair(item1, item2)
        assert conf == 0.0

    def test_score_pair_rejects_different_countries(self):
        from scripts.find_duplicate_equipment import _score_pair

        item1 = {"common_name": "Tiger Tank", "country_of_origin": "Germany"}
        item2 = {"common_name": "Tiger Tank", "country_of_origin": "USA"}
        conf, _, _ = _score_pair(item1, item2)
        assert conf == 0.0

    def test_score_pair_matches_similar(self):
        from scripts.find_duplicate_equipment import _score_pair

        item1 = {
            "common_name": "M4 Sherman",
            "country_of_origin": "USA",
            "category": "Tank",
        }
        item2 = {
            "common_name": "M4A1 Sherman",
            "country_of_origin": "USA",
            "category": "Tank",
        }
        conf, reasons, _ = _score_pair(item1, item2)
        assert conf > 0.3
        assert len(reasons) > 0


class TestFindDuplicatePlaces:
    """Tests for scripts/find_duplicate_places_v2.py helpers."""

    def test_normalize_strips_accents(self):
        from scripts.find_duplicate_places_v2 import _normalize

        assert _normalize("Grémecey") == "gremecey"

    def test_normalize_strips_fillers(self):
        from scripts.find_duplicate_places_v2 import _normalize

        assert "bois" in _normalize("Bois de Fréménil")
        assert " of " not in _normalize("Battle of the Bulge")

    def test_similarity_identical(self):
        from scripts.find_duplicate_places_v2 import _similarity

        assert _similarity("Nancy", "Nancy") == 1.0

    def test_similarity_with_geo_terms(self):
        from scripts.find_duplicate_places_v2 import _similarity

        # "Fort X" vs "X" should still match but with reduced score
        score = _similarity("Fort Driant", "Driant")
        assert score > 0.5

    def test_haversine_same_point(self):
        from scripts.find_duplicate_places_v2 import _haversine_km

        assert _haversine_km(48.69, 6.18, 48.69, 6.18) == 0.0

    def test_haversine_known_distance(self):
        from scripts.find_duplicate_places_v2 import _haversine_km

        # Paris to Nancy ~280km
        dist = _haversine_km(48.86, 2.35, 48.69, 6.18)
        assert 270 < dist < 300

    def test_get_coords_flat(self):
        from scripts.find_duplicate_places_v2 import _get_coords

        lat, lon = _get_coords({"latitude": 48.69, "longitude": 6.18})
        assert lat == 48.69
        assert lon == 6.18

    def test_get_coords_nested(self):
        from scripts.find_duplicate_places_v2 import _get_coords

        lat, lon = _get_coords({"coordinates": {"latitude": 48.69, "longitude": 6.18}})
        assert lat == 48.69
        assert lon == 6.18

    def test_get_coords_missing(self):
        from scripts.find_duplicate_places_v2 import _get_coords

        lat, lon = _get_coords({})
        assert lat is None
        assert lon is None


class TestFindDuplicateGroups:
    """Tests for scripts/find_duplicate_groups.py helpers."""

    def test_normalize(self):
        from scripts.find_duplicate_groups import _normalize

        assert _normalize("The 4th Infantry Division") == "4th infantry division"
        assert _normalize("U.S. Third Army") == "third army"

    def test_is_substring_match(self):
        from scripts.find_duplicate_groups import _is_substring_match

        assert _is_substring_match("4th Infantry", "4th Infantry Division") is True
        assert _is_substring_match("4th Infantry", "7th Armored") is False

    def test_similarity(self):
        from scripts.find_duplicate_groups import _similarity

        assert _similarity("4th Infantry Division", "4th Infantry Div") > 0.8
        assert _similarity("4th Infantry", "7th Armored") < 0.5
