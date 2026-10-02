"""OpenSERP people: query augmentation with People-JSON facts + result skipping."""

from src.enrichment.openserp_enrichment import _person_query_terms, _skip_result


def test_person_query_terms_from_facts():
    data = {
        "biographical_profile": {
            "nationality": "USA",
            "units_served": [
                {
                    "unit": "9th Infantry Division",
                    "designation": "9th Infantry Division",
                }
            ],
        }
    }
    terms = _person_query_terms(data)
    assert "9th Infantry Division" in terms
    assert "USA" in terms


def test_person_query_terms_empty_when_no_facts():
    assert _person_query_terms({}) == ""
    assert _person_query_terms({"biographical_profile": {}}) == ""


def test_skip_result_award_domains_and_ibiblio():
    # award-source domain -> skipped
    assert _skip_result("https://valor.militarytimes.com/recipient/recipient-1/")
    # ibiblio -> skipped (owner exclusion)
    assert _skip_result("https://www.ibiblio.org/hyperwar/")
    assert _skip_result("http://ibiblio.org/pha/")
    # a normal source -> kept
    assert not _skip_result("https://en.wikipedia.org/wiki/Audie_Murphy")
    assert not _skip_result("")
