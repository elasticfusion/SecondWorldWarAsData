"""Title-derived civilian group memberships: born date-unverified, concurrent, temporal
validation flips, never asserts unverified as fact; dedup discounts unverified."""

import importlib.util
import sys
from pathlib import Path

from src.extraction.people import BiographicalProfile, GroupAffiliation
from src.extraction.title_memberships import (
    derive_title_memberships,
    enrich_title_memberships,
    validate_membership_dates,
)

_SPEC = importlib.util.spec_from_file_location(
    "fdp_tm", Path(__file__).parent.parent / "scripts" / "find_duplicate_people.py"
)
fdp = importlib.util.module_from_spec(_SPEC)
sys.modules["fdp_tm"] = fdp
_SPEC.loader.exec_module(fdp)


def _rep():
    return {
        "name": "John Smith",
        "biographical_profile": {
            "biographical_details": "Representative John Smith from New Jersey, a Republican."
        },
        "event_mentions": [{"date": "1943-06"}],
    }


def test_concurrent_memberships_all_born_unverified():
    affs = derive_title_memberships(_rep())
    groups = {a["group"] for a in affs}
    assert "House of Representatives" in groups
    assert "New Jersey" in groups
    assert "Republican Party" in groups
    # correctness guard: NONE asserted as fact
    assert all(a["date_verified"] is False for a in affs)
    assert all(a["implied_from_title"] is True for a in affs)
    assert all(a["as_of_source_date"] == "1943" for a in affs)


def test_temporal_validation_flips_only_confirmed():
    person = _rep()
    enrich_title_memberships(person)

    class FakeGrok:
        def chat_completion(self, p):
            return "YES" if "House of Representatives" in p else "NO"

    validate_membership_dates(person, FakeGrok())
    affs = {a["group"]: a for a in person["biographical_profile"]["group_affiliations"]}
    assert affs["House of Representatives"]["date_verified"] is True
    assert affs["Republican Party"]["date_verified"] is False  # NO -> not asserted


def test_schema_backcompat_and_version():
    bp = BiographicalProfile(nationality="USA")
    assert bp.group_affiliations == []
    ga = GroupAffiliation(group="Senate", implied_from_title=True, date_verified=False)
    assert ga.group == "Senate" and ga.date_verified is False
    from src.schemas import SCHEMA_VERSION

    major, minor = (int(x) for x in SCHEMA_VERSION.split(".")[:2])
    assert (major, minor) >= (2, 6)


def test_dedup_discounts_unverified_membership():
    def person(verified):
        return {
            "biographical_profile": {
                "group_affiliations": [
                    {
                        "group": "Senate",
                        "group_kind": "legislature",
                        "date_verified": verified,
                    }
                ]
            }
        }

    cfg = __import__(
        "src.dedup.config", fromlist=["default_dedup_config"]
    ).default_dedup_config()
    _, unv = fdp._shared_group_affiliation(person(False), person(False), cfg)
    _, ver = fdp._shared_group_affiliation(person(True), person(True), cfg)
    assert 0 < unv < ver  # unverified is a weaker (discounted) corroborator


def test_dedup_party_is_near_noise():
    cfg = __import__(
        "src.dedup.config", fromlist=["default_dedup_config"]
    ).default_dedup_config()

    def p():
        return {
            "biographical_profile": {
                "group_affiliations": [
                    {
                        "group": "Republican Party",
                        "group_kind": "party",
                        "date_verified": True,
                    }
                ]
            }
        }

    _, w = fdp._shared_group_affiliation(p(), p(), cfg)
    assert w <= 0.05  # a shared national party is ~noise
