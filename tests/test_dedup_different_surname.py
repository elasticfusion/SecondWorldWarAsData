"""A shared first name is not a possible duplicate: two full names with different surnames are two
people (owner rule, 2026-11-10, Cross-Channel Attack dedup review: "William E. Dreher, Jr." and
"William R. Benn, Jr." were offered as a pair on a shared "William ... Jr.", a shared position and
a nearby mention). The pairs below are this run's own review list."""

from unittest.mock import patch

import pytest

import src.dedup.find_duplicate_people as fdp
from src.dedup.config import load_dedup_config

CFG = load_dedup_config(None)


def _score(a: str, b: str, **extra):
    p1, p2 = {"name": a, **extra.get("p1", {})}, {"name": b, **extra.get("p2", {})}
    # the evidence that put these pairs on the review list: a shared position
    with patch.object(fdp, "_has_shared_positions", return_value=True):
        return fdp._score_pair(p1, p2, {}, CFG)


@pytest.mark.parametrize(
    "a, b",
    [
        ("William E. Dreher, Jr.", "William R. Benn, Jr."),
        ("George H. Bowen", "George M. Eberle"),
        ("John D. Bolderson", "John L. Benton"),
        ("Kenneth Bleau", "Kenneth E. Beitler"),
        ("Leonard A. Anker", "Leonard P. Elder"),
        ("William A. Anderson", "William M. Evans"),
        ("William M. Evans", "William T. Dillon"),
    ],
)
def test_a_shared_first_name_is_not_a_possible_duplicate(a, b):
    reasons, score = _score(a, b)
    assert score < CFG.candidate_threshold, reasons
    assert any(r.startswith("Different surnames") for r in reasons)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Arthur Tedder", "Arthur W. Tedder"),
        ("Bertram H. Ramsay", "Bertram Ramsay"),
        ("Norman Cota", "Norman D. Cota"),
        ("Charles D. Canham", "Charles D. W. Canham"),
        ("R. G. Ruppenthal", "Roland G. Ruppenthal"),
        ("Bernard Paget", "Paget"),  # a bare surname is no full name: no veto
        ("Gerd von Rundstedt", "Rundstedt"),
        ("Fritz Schwarzwalder", "Fritz Schwarzwaldor"),  # an OCR variant of one surname
        ("Hans Hoffman", "Hans Hoffmann"),
    ],
)
def test_the_same_surname_or_a_variant_of_it_is_still_offered(a, b):
    reasons, score = _score(a, b)
    assert score >= CFG.candidate_threshold, reasons
    assert not any(r.startswith("Different surnames") for r in reasons)


@pytest.mark.parametrize(
    "a, b",
    [
        ("Bernard Law Montgomery", "Viscount Montgomery of Alamein"),  # a title
        ("Trafford Leigh-Mallory", "Trafford Leigh Mallory"),  # OCR dropped the hyphen
        ("Trafford Leigh-Mallory", "Air Chief Marshal Mallory"),
        ("Hideki Tojo", "Tojo Hideki"),  # family name first
        ("Sir Alan Brooke", "Alan Brooke, Viscount Alanbrooke"),  # a peerage
    ],
)
def test_one_persons_surname_in_another_form_is_never_vetoed(a, b):
    reasons, _ = _score(a, b)
    assert not any(r.startswith("Different surnames") for r in reasons), reasons


def test_an_identity_signal_lifts_the_veto():
    url = {"wikipedia_url": "https://en.wikipedia.org/wiki/Someone"}
    reasons, score = _score("Mary A. Smith", "Mary A. Jones", p1=url, p2=url)
    assert score >= CFG.candidate_threshold
    assert not any(r.startswith("Different surnames") for r in reasons)


def test_ranks_and_suffixes_are_not_names():
    assert fdp._extract_last_name("Lt. Col. Robert G. Cole, Jr.") == "cole"
    assert fdp._extract_last_name("William E. Dreher, Jr.") == "dreher"
    assert not fdp._is_full_name("Paget") and fdp._is_full_name("R. G. Ruppenthal")


def test_the_veto_is_configurable():
    cfg = load_dedup_config(
        {
            "dedup": {
                "people": {
                    "different_surname_weight": -1.5,
                    "surname_variant_ratio": 0.9,
                }
            }
        }
    )
    assert cfg.different_surname_weight == -1.5 and cfg.surname_variant_ratio == 0.9
    r, s = fdp._check_different_surname(
        "A B Smith", "A B Jones", "smith", "jones", cfg, identity=False
    )
    assert s == -1.5 and r
