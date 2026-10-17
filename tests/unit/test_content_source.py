"""Content-source path scheme: SOURCE id in contentrepository/ paths distinguishes
system/operator-ingested material from (future) contributor uploads, backward-compatibly.
"""

from src.utils.content_source import (
    SYSTEM_SOURCE_ID,
    is_source_segment,
    parse_content_key,
)


def test_system_source_id_is_reserved_safe_token():
    assert SYSTEM_SOURCE_ID == "_system"


def test_legacy_unprefixed_path_is_system():
    # the existing corpus layout -> system-provided, title = first segment
    assert parse_content_key("contentrepository/RoerDams/chapter2/x.md") == (
        "_system",
        "RoerDams",
    )
    assert parse_content_key("contentrepository/RoerDams/x.md") == (
        "_system",
        "RoerDams",
    )


def test_explicit_system_segment():
    assert parse_content_key("contentrepository/_system/RoerDams/x.md") == (
        "_system",
        "RoerDams",
    )


def test_contributor_sources():
    assert parse_content_key("contentrepository/user-a1b2/MyDiary/scan.pdf") == (
        "user-a1b2",
        "MyDiary",
    )
    assert parse_content_key("contentrepository/contrib-x/Doc/f.md") == (
        "contrib-x",
        "Doc",
    )
    assert parse_content_key("contentrepository/org-nara/UnitJnl/j.md") == (
        "org-nara",
        "UnitJnl",
    )


def test_loose_and_non_content_keys_default_system():
    assert parse_content_key("contentrepository/Foo.md") == ("_system", "Foo")
    assert parse_content_key("output/bibliography/x.json")[0] == "_system"
    assert parse_content_key("") == ("_system", "")


def test_is_source_segment_distinguishes_source_from_title():
    assert is_source_segment("_system") is True
    assert is_source_segment("user-abc") is True
    assert is_source_segment("org-nara") is True
    # a normal title is NOT a source segment (so legacy paths parse as title)
    assert is_source_segment("RoerDams") is False
    assert is_source_segment("BreakoutAndPursuit") is False
