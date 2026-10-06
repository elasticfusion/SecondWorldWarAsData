"""crew_accounts (narrative, source-tracked, person-linked) + Group A reference facts
(timeline/technical_evolution/logistics, enrichment-sourced with source stamping)."""

import jsonschema

from src.extraction.equipment import (
    CrewAccountInput,
    _link_crew_accounts,
    _resolve_person_id,
    _merge_source_tracked_reference,
    _merge_crew_accounts,
)
from src.schemas.equipment_output import EQUIPMENT_OUTPUT_SCHEMA as S

UL = "01ABCDEFGH0123456789ABCDEF"


def test_crew_account_requires_original_text():
    accts = [
        CrewAccountInput(
            person_name="A", observations="x"
        ),  # no original_text -> dropped
        CrewAccountInput(
            person_name="Belton Cooper",
            observations="transmission failures",
            original_text="Cooper noted...",
            book="Death Traps",
        ),
    ]
    out = _link_crew_accounts(accts, {})
    assert len(out) == 1  # untraceable account dropped
    assert out[0]["original_text"] and out[0]["book"] == "Death Traps"


def test_crew_account_person_link_exact_and_fuzzy():
    idx = {"Belton Cooper": UL}
    out = _link_crew_accounts(
        [CrewAccountInput(person_name="belton cooper", original_text="t")], idx
    )
    assert out[0]["PersonID"] == UL  # case-insensitive exact
    out2 = _link_crew_accounts(
        [CrewAccountInput(person_name="Belton Cooper", original_text="t")], idx
    )  # typo
    assert out2[0]["PersonID"] == UL  # fuzzy


def test_person_id_fuzzy_rejects_distant():
    assert _resolve_person_id("Dwight Eisenhower", {"Belton Cooper": UL}) is None


def test_reference_facts_stamped_with_source():
    data = {}
    enriched = {
        "wikipedia_url": "https://en.wikipedia.org/wiki/M4_Sherman",
        "timeline": {"first_production": "1942-02", "total_produced": 49234},
        "technical_evolution": [{"date": "1943-02", "change": "wet stowage"}],
        "logistics": {"ammunition_capacity": "90 rounds"},
    }
    _merge_source_tracked_reference(data, enriched)
    assert data["timeline"]["source"] == "wikipedia"
    assert data["timeline"]["source_url"].endswith("M4_Sherman")
    assert data["technical_evolution"][0]["source"] == "wikipedia"
    assert data["logistics"]["source"] == "wikipedia"


def test_reference_source_prefers_grokipedia():
    data = {}
    _merge_source_tracked_reference(
        data, {"grokipedia_url": "https://grokipedia.com/M4", "logistics": {"x": "y"}}
    )
    assert data["logistics"]["source"] == "grokipedia"


def test_crew_accounts_merge_dedup():
    existing = {"crew_accounts": [{"person_name": "Cooper", "original_text": "t1"}]}
    incoming = {
        "crew_accounts": [
            {"person_name": "Cooper", "original_text": "t1"},  # dup
            {"person_name": "Cooper", "original_text": "t2"},
        ]
    }  # new (different source text)
    _merge_crew_accounts(existing, incoming)
    assert (
        len(existing["crew_accounts"]) == 2
    )  # conflict/extra kept, exact dup collapsed


def test_schema_validates_source_tracked_fields():
    rec = {
        "EquipmentID": UL,
        "crew_accounts": [
            {
                "PersonID": UL,
                "person_name": "Cooper",
                "role": "maint",
                "observations": "x",
                "original_text": "t",
                "book": "Death Traps",
            }
        ],
        "timeline": {
            "total_produced": 49234,
            "source": "wikipedia",
            "source_url": "https://x",
        },
        "technical_evolution": [
            {
                "date": "1943",
                "change": "y",
                "source": "grokipedia",
                "source_url": "https://x",
            }
        ],
        "logistics": {
            "fuel_consumption": "1 mpg",
            "source": "wikipedia",
            "source_url": "https://x",
        },
    }
    jsonschema.validate(rec, S)
