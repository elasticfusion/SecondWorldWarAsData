"""Centralized result dedup: URL-identity upsert, corroboration, never-downgrade, history."""

from src.utils.result_dedup import dedup_entity


def test_same_url_collapses_and_adds_history():
    data = {
        "images": [
            {"url": "https://ex.com/p.jpg", "title": "a"},
            {
                "url": "https://ex.com/p.jpg?utm_source=x",
                "title": "a",
            },  # tracking variant = same
        ]
    }
    dedup_entity(data, "people")
    assert len(data["images"]) == 1
    assert data["images"][0]["history"]  # observation recorded
    assert data["images"][0]["checked_at"]


def test_different_urls_preserved_corroboration():
    data = {
        "web_results": [
            {"url": "https://a.com/churchill", "claim": "initiated Dunkirk rescue"},
            {
                "url": "https://b.com/churchill",
                "claim": "initiated Dunkirk rescue",
            },  # 2nd source
        ]
    }
    dedup_entity(data, "people_groups")
    assert len(data["web_results"]) == 2  # corroboration kept


def test_never_downgrade_keeps_richer_stored():
    data = {
        "images": [
            {"url": "https://ex.com/x", "title": "detailed caption", "credit": "NARA"},
            {
                "url": "https://ex.com/x",
                "title": "",
            },  # thinner re-fetch -> must not overwrite
        ]
    }
    dedup_entity(data, "places")
    img = data["images"][0]
    assert img["title"] == "detailed caption" and img["credit"] == "NARA"


def test_upgrade_only_fills_absent():
    data = {
        "images": [
            {"url": "https://ex.com/x", "title": ""},
            {
                "url": "https://ex.com/x",
                "title": "now has a caption",
            },  # richer -> upgrade
        ]
    }
    dedup_entity(data, "places")
    assert data["images"][0]["title"] == "now has a caption"


def test_idempotent():
    data = {"images": [{"url": "https://ex.com/x", "title": "a"}]}
    dedup_entity(data, "places")
    n1 = len(data["images"])
    dedup_entity(data, "places")
    assert len(data["images"]) == n1  # re-running never grows


def test_resource_urls_string_list_dedup():
    data = {
        "resource_urls": [
            "https://archive.org/x",
            "https://archive.org/x?utm_medium=email",  # tracking variant = same
            "https://archive.org/y",
        ]
    }
    dedup_entity(data, "bibliography")
    assert len(data["resource_urls"]) == 2


def test_unknown_entity_noop():
    data = {"images": [{"url": "https://ex.com/x"}, {"url": "https://ex.com/x"}]}
    dedup_entity(data, "weather")  # no DEDUP_FIELDS -> untouched
    assert len(data["images"]) == 2


def test_bibliography_write_not_blocked_by_dedup(monkeypatch):
    """B1 regression: dedup must NOT stamp recheck fields onto strict-schema item types
    (mentions = additionalProperties:false). A bibliography record WITH mentions +
    duplicate resource_urls must still PASS the central write guard after dedup."""
    import os

    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    from pathlib import Path
    from src.utils.file_lock import _validate_entity

    rec = {
        "BibliographyID": "01HQ3M8R9V2X7YABCDEFGH0JKM",
        "title": "Siegfried Line Campaign",
        "resource_urls": ["https://archive.org/x", "https://archive.org/x"],  # dup
        "mentions": [
            {
                "MentionID": "01HQ3M8R9V2X7YABCDEFGH0JKN",
                "book": "A",
                "verbatim_reference": "FUSA G-2 file",
            }
        ],
    }
    ok = _validate_entity(Path("output/bibliography/x.json"), rec, record_stats=False)
    assert ok is True  # write NOT blocked
    assert len(rec["resource_urls"]) == 1  # resource_urls deduped
    assert (
        "checked_at" not in rec["mentions"][0]
    )  # mentions NOT stamped (strict schema)


def test_people_academic_references_deduped(monkeypatch):
    data = {
        "academic_references": [
            {"url": "https://a.edu/x", "title": "t"},
            {"url": "https://a.edu/x?utm_source=y", "title": "t"},  # same
        ]
    }
    dedup_entity(data, "people")
    assert len(data["academic_references"]) == 1
