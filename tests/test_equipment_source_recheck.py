"""Equipment source-recheck: recover country_of_origin (dedup veto) + category from the
retained event_mentions original_text, via the reusable SourceRechecker. And verify the
enforced schema now DECLARES original_text so retention is contractual."""

from src.extraction.equipment_source_recheck import recheck_equipment_from_source
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA


class _Grok:
    def __init__(self, r):
        self._r = r

    def extract_json(self, prompt, use_cache=True, cache_type=""):
        return self._r


def _equip(name, text, **extra):
    d = {"common_name": name, "event_mentions": [{"original_text": text}]}
    d.update(extra)
    return d


def test_recovers_country_of_origin_from_source():
    e = _equip("Panther", "The German Panther tanks counterattacked.", category="armor")
    n = recheck_equipment_from_source(e, _Grok({"country_of_origin": "DEU"}))
    assert n == 1
    assert e["country_of_origin"] == "DEU"
    assert e["_provenance"]["country_of_origin"]["sourced_from"] == "original_text"


def test_recovers_category_too():
    e = _equip("Sherman", "Sherman tanks, American armor, advanced.")
    n = recheck_equipment_from_source(
        e, _Grok({"country_of_origin": "USA", "category": "armor"})
    )
    assert n == 2 and e["category"] == "armor"


def test_gap_fill_only_never_overwrites():
    e = _equip("Tiger", "text", country_of_origin="DEU", category="armor")
    assert recheck_equipment_from_source(e, _Grok({"country_of_origin": "USA"})) == 0
    assert e["country_of_origin"] == "DEU"


def test_fail_safe_on_error():
    class _Boom:
        def extract_json(self, **k):
            raise RuntimeError("down")

    e = _equip("Panther", "German Panther")
    assert recheck_equipment_from_source(e, _Boom()) == 0
    assert "country_of_origin" not in e


def test_schema_declares_original_text_for_retention():
    mention = EQUIPMENT_OUTPUT_SCHEMA["properties"]["event_mentions"]["items"][
        "properties"
    ]
    assert (
        "original_text" in mention
    ), "original_text must be declared for source-recheck"
