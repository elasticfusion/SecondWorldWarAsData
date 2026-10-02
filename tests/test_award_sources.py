"""Tests for authoritative award-citation sourcing (schema + interface + offline)."""

import json
from pathlib import Path

from src.enrichment.award_offline_source import OfflineAwardDataset
from src.enrichment.award_sources import (
    enrich_person_awards,
    has_award_context,
    is_us_person,
    should_source_awards,
)
from src.extraction.people import MilitaryAward

# --- schema provenance (migration-safe) ---


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
