"""Source-recheck gap-fill: recover missing nationality / CC parent division from the
retained original_text (source-first, gap-fill-only, fail-safe)."""

from src.extraction.group_source_recheck import recheck_group_from_source


class _Grok:
    def __init__(self, result):
        self._r = result

    def extract_json(self, prompt, use_cache=True, cache_type=""):
        return self._r


def _group(name, text, **extra):
    d = {"name": name, "event_mentions": [{"original_text": text, "book": "Some Book"}]}
    d.update(extra)
    return d


def test_recovers_nationality_from_source_text():
    g = _group(
        "2nd Armored Division", "The German 2nd Armored Division attacked at dawn."
    )
    n = recheck_group_from_source(g, _Grok({"nationality": "DEU"}))
    assert n == 1
    assert g["nationality"] == "DEU"
    assert g["_provenance"]["nationality"]["sourced_from"] == "original_text"


def test_recovers_cc_parent_division_from_source():
    g = _group("CCB", "CCB of the 3rd Armored Division, under Col. Smith, advanced.")
    n = recheck_group_from_source(
        g, _Grok({"parent_organization": "3rd Armored Division"})
    )
    assert n >= 1
    assert "3rd Armored Division" in g["parent_organization"]


def test_gap_fill_only_never_overwrites():
    # already has nationality -> not a CC -> nothing to recheck
    g = _group("2nd Division", "text", nationality="USA")
    assert recheck_group_from_source(g, _Grok({"nationality": "DEU"})) == 0
    assert g["nationality"] == "USA"


def test_book_hint_when_text_yields_nothing():
    g = {
        "name": "9th Division",
        "event_mentions": [
            {"original_text": "advanced east", "book": "Cross-Channel Attack"}
        ],
    }
    # Grok returns null nationality -> fall back to the source-book hint (USA)
    n = recheck_group_from_source(g, _Grok({"nationality": None}))
    assert n == 1 and g["nationality"] == "USA"
    assert g["_provenance"]["nationality"]["sourced_from"] == "source_book_hint"


def test_fail_safe_on_grok_error():
    class _Boom:
        def extract_json(self, **k):
            raise RuntimeError("down")

    g = _group("2nd Armored Division", "German 2nd Armored Division")
    # error -> no crash; book hint not applicable (unknown book) -> 0, record unchanged
    assert recheck_group_from_source(g, _Boom()) == 0
    assert "nationality" not in g


def test_noop_when_nothing_missing():
    g = _group("1st Infantry Division", "text", nationality="USA")
    assert recheck_group_from_source(g, _Grok({})) == 0
