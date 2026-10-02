"""Tests for authoritative award-citation sourcing (schema + interface + offline)."""

import json
from pathlib import Path

from src.enrichment.award_offline_source import OfflineAwardDataset
from src.enrichment.award_sources import (
    AwardCitation,
    award_source_nationality,
    awarding_power,
    enrich_person_awards,
    has_award_context,
    is_us_person,
    should_source_awards,
)
from src.extraction.people import MilitaryAward

# --- schema provenance (migration-safe) ---


def test_awarding_power_classifies_issuing_nation():
    assert awarding_power("Medal of Honor") == "USA"
    assert awarding_power("Victoria Cross") == "GBR"
    assert awarding_power("Iron Cross 2nd Class") == "DEU"
    assert awarding_power("Knight's Cross of the Iron Cross") == "DEU"
    assert awarding_power("Croix de Guerre 1939-1945") == "FRA"
    assert awarding_power("Medaglia d'Oro al Valor Militare") == "ITA"
    assert awarding_power("Totally Made Up Medal") is None
    assert awarding_power(None) is None


def test_foreign_national_routes_by_awarding_power_not_nationality():
    """A Frenchman who SERVED under Germany (nationality_served=DEU) routes his Iron
    Cross to the German record system; routing is confined to indicated countries."""
    # Serving country indicated -> Iron Cross routes to DEU.
    served = {
        "biographical_profile": {
            "nationality": "French",
            "nationality_served": "German",
        }
    }
    iron_cross = {"award": "Iron Cross 1st Class"}
    assert award_source_nationality(iron_cross, served) == "DEU"
    # An unrecognized award falls back to the first indicated country (FRA).
    unknown = {"award": "Some Regimental Token"}
    assert award_source_nationality(unknown, served) == "FRA"
    # A Frenchman with NO serving country indicated: his Iron Cross must NOT route to
    # a country he doesn't indicate -> falls back to his only indicated country, FRA.
    fra_only = {"biographical_profile": {"nationality": "French"}}
    assert award_source_nationality(iron_cross, fra_only) == "FRA"


def test_gate_requires_indicated_country_not_award_name_alone():
    """A person who indicates NO nationality/serving country is NOT sourced, even with
    a recognizable award — the award name alone must not broaden sourcing."""
    person = {
        "biographical_profile": {
            "nationality": "Freedonia",  # not a recognized country code
            "military_awards": [{"award": "Iron Cross 2nd Class"}],
        }
    }
    assert should_source_awards(person) is False
    # But indicating a serving country admits him (and routing is confined to it).
    person["biographical_profile"]["nationality_served"] = "German"
    assert should_source_awards(person) is True


def test_enrich_routes_per_award_via_selector():
    """enrich_person_awards accepts a selector(nationality)->sources and routes each
    award by its awarding power."""
    de_source = _StubSource(
        "DE-Stub",
        [
            AwardCitation(
                "Für Tapferkeit",
                "Bundesarchiv",
                "u",
                "2026-10-02",
                award="Iron Cross",
                language="German",
                verified=True,
            )
        ],
    )

    def selector(code):
        return [de_source] if code == "DEU" else []

    person = {
        "name": "Jean Dupont",
        "biographical_profile": {
            "nationality": "French",
            "nationality_served": "German",  # served under Germany -> DEU routing allowed
            "military_awards": [{"award": "Iron Cross"}],
        },
    }
    filled = enrich_person_awards(person, selector)
    assert filled == 1
    award = person["biographical_profile"]["military_awards"][0]
    assert award["source_name"] == "Bundesarchiv"
    # German text preserved as original (translation disabled in test env).
    assert award.get("citation_language") == "German"
    assert award.get("citation_text_original") == "Für Tapferkeit"


class _StubSource:
    def __init__(self, name, citations):
        self.name = name
        self._citations = citations

    def lookup(self, person_name, award_hint=""):
        return list(self._citations)


# --- original tests below ---


def test_military_award_backcompat_and_provenance():
    old = MilitaryAward(award="Distinguished Service Cross")
    assert old.award and old.citation_text is None  # old records still valid
    new = MilitaryAward(
        award="DSC",
        citation_text="For extraordinary heroism...",
        source_name="American War Library",
        source_url="https://awl/doe",
        retrieved_date="2026-10-02",
        verified=True,
    )
    assert new.verified is True and new.source_name == "American War Library"


# --- gating ---


def test_is_us_person_spellings():
    for v in ("USA", "us", "United States", "American"):
        assert is_us_person(v)
    for v in ("Germany", "", None):
        assert not is_us_person(v)


def test_gate_requires_us_and_award_context():
    us_award = {
        "biographical_profile": {
            "nationality": "USA",
            "military_awards": [{"award": "DSC"}],
        }
    }
    assert should_source_awards(us_award) is True
    assert has_award_context(us_award) is True
    # Germany is now a REGISTERED nationality (multi-country) → in scope.
    assert should_source_awards(
        {
            "biographical_profile": {
                "nationality": "Germany",
                "military_awards": [{"award": "Knights Cross"}],
            }
        }
    )
    # Unknown/unregistered nationality → gated out.
    assert not should_source_awards(
        {
            "biographical_profile": {
                "nationality": "Freedonia",
                "military_awards": [{"award": "x"}],
            }
        }
    )
    # Registered nationality but no award context → gated out.
    assert not should_source_awards(
        {"biographical_profile": {"nationality": "USA", "military_awards": []}}
    )


# --- offline dataset source ---


def _dataset(tmp_path: Path) -> OfflineAwardDataset:
    ds = tmp_path / "dsc.json"
    ds.write_text(
        json.dumps(
            [
                {
                    "name": "John A. Doe",
                    "award": "Distinguished Service Cross",
                    "citation": "For extraordinary heroism in action near Aachen.",
                    "source_name": "American War Library",
                    "source_url": "https://awl/doe",
                }
            ]
        ),
        encoding="utf-8",
    )
    return OfflineAwardDataset(ds, "American War Library")


def test_offline_fills_citation_with_provenance(tmp_path):
    src = _dataset(tmp_path)
    person = {
        "name": "John A. Doe",
        "biographical_profile": {
            "nationality": "USA",
            "military_awards": [{"award": "Distinguished Service Cross"}],
        },
    }
    filled = enrich_person_awards(person, [src])
    award = person["biographical_profile"]["military_awards"][0]
    assert filled == 1
    assert award["citation_text"].startswith("For extraordinary heroism")
    assert award["source_name"] == "American War Library"
    assert award["source_url"] == "https://awl/doe"
    assert award["verified"] is True
    assert award["retrieved_date"]  # stamped


def test_offline_does_not_overwrite_existing_citation(tmp_path):
    src = _dataset(tmp_path)
    person = {
        "name": "John A. Doe",
        "biographical_profile": {
            "nationality": "USA",
            "military_awards": [
                {"award": "Distinguished Service Cross", "citation_text": "existing"}
            ],
        },
    }
    assert enrich_person_awards(person, [src]) == 0  # gap-fill only
    assert (
        person["biographical_profile"]["military_awards"][0]["citation_text"]
        == "existing"
    )


def test_offline_conservative_name_match(tmp_path):
    src = _dataset(tmp_path)
    # Different person, same last name + different initial -> no match.
    person = {
        "name": "Robert Doe",
        "biographical_profile": {
            "nationality": "USA",
            "military_awards": [{"award": "Distinguished Service Cross"}],
        },
    }
    assert enrich_person_awards(person, [src]) == 0


def test_offline_missing_dataset_is_empty_source(tmp_path):
    src = OfflineAwardDataset(tmp_path / "nope.json", "X")
    assert src.lookup("Anyone") == []
