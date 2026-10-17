"""HyperWar bibliographical-note resolution source."""

from src.enrichment.hyperwar_biblio import (
    resolve_from_biblio_note,
    volume_matches,
)

_SIEG = (
    "https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/USA-E-Siegfried-3.html#fn1"
)


def test_volume_matches():
    assert volume_matches(_SIEG)
    assert not volume_matches("https://example.com/other.html")
    assert not volume_matches("")


def test_published_work_resolves_to_canonical_identity():
    r = resolve_from_biblio_note("Bradley, A Soldier's Story, p. 40.", _SIEG)
    assert r and r["biblio_category"] == "published_work"
    assert r["canonical_title"] == "A Soldier's Story"
    assert r["canonical_author"].startswith("Bradley")


def test_published_work_eisenhower():
    r = resolve_from_biblio_note("Eisenhower, Crusade in Europe.", _SIEG)
    assert r and r["canonical_title"] == "Crusade in Europe"


def test_sylvan_diary_is_archival_ocmh():
    r = resolve_from_biblio_note("Sylvan Diary, passim.", _SIEG)
    assert r and r["biblio_category"] == "archival"
    assert "OCMH" in r["holding"]


def test_german_ms_is_archival_ocmh():
    r = resolve_from_biblio_note("MS # B-810, Generalleutnant Hans Schmidt.", _SIEG)
    assert r and r["biblio_category"] == "archival" and "OCMH" in r["holding"]


def test_combat_interview_is_nara():
    r = resolve_from_biblio_note("Interv with Collins, 25 Jan 54.", _SIEG)
    assert r and "National Archives" in r["holding"]


def test_unit_records_are_nara():
    r = resolve_from_biblio_note("5th Armd Div G-2 Per Rpt 85, 24 Oct.", _SIEG)
    assert r and "National Archives" in r["holding"]


def test_non_covered_url_returns_none():
    assert (
        resolve_from_biblio_note("Bradley, A Soldier's Story.", "https://x/y.html")
        is None
    )


def test_resolver_attaches_biblio_note_provenance():
    """End-to-end wiring: resolve_bibliography_entry enriches an archival citation with the
    holding institution from the biblio note (source_url on the mention)."""
    import src.enrichment.bibliography_resolver as br

    entry = {
        "citation": {"title": "Unknown"},
        "mentions": [
            {
                "verbatim_reference": "Sylvan Diary, passim.",
                "source_url": _SIEG,
                "reference_number": "3",
            }
        ],
    }
    # No NARA key / no online search needed — just assert the note enrichment ran.
    br.resolve_bibliography_entry(entry, grok_client=None, config={"nara_api_key": ""})
    sm = entry.get("source_metadata") or {}
    assert sm.get("biblio_category") == "archival"
    assert "OCMH" in sm.get("holding", "")


# --- Multi-volume generalization (Cross-Channel Attack + Logistics) ---
_XCHAN = "https://www.ibiblio.org/hyperwar/USA/USA-E-XChannel/USA-E-XChannel-I.html#fn4"
_LOG2 = (
    "https://www.ibiblio.org/hyperwar/USA/USA-E-Logistics2/USA-E-Logistics2-5.html#fn2"
)


def test_any_hyperwar_volume_matches():
    assert volume_matches(_XCHAN)
    assert volume_matches(_LOG2)
    assert volume_matches(
        "https://www.ibiblio.org/hyperwar/USA/USA-E-Ardennes/USA-E-Ardennes-2.html"
    )


def test_xchannel_published_work_butcher():
    r = resolve_from_biblio_note(
        "Butcher, My Three Years With Eisenhower, p. 500.", _XCHAN
    )
    assert r and r["biblio_volume"] == "USA-E-XChannel"
    assert r["canonical_title"] == "My Three Years With Eisenhower"
    assert r["biblio_note_url"].endswith("USA-E-XChannel-Biblio.html")


def test_xchannel_shaef_is_archival():
    r = resolve_from_biblio_note("SHAEF SGS 381, Vol. I.", _XCHAN)
    assert (
        r
        and r["biblio_category"] == "archival"
        and "SHAEF" in r["description"]
        or "AGO" in r["holding"]
    )


def test_logistics_published_work_and_note_url():
    r = resolve_from_biblio_note("Bradley, A Soldier's Story.", _LOG2)
    assert r and r["biblio_volume"] == "USA-E-Logistics2"
    assert r["canonical_title"] == "A Soldier's Story"
    assert r["biblio_note_url"].endswith("USA-E-Logistics2-Biblio.html")


def test_logistics_general_board_category():
    r = resolve_from_biblio_note("General Board Report No. 27, Supply.", _LOG2)
    assert r and r["biblio_category"] == "archival" and "OCMH" in r["holding"]


def test_logistics_service_journal_category():
    r = resolve_from_biblio_note(
        "Smith, 'Supply in the ETO', Military Review, Mar 46.", _LOG2
    )
    assert r and "service journal" in r["holding"].lower()


def test_generic_categories_work_across_volumes():
    # German MS resolves the same way regardless of volume.
    for url in (_SIEG, _XCHAN, _LOG2):
        r = resolve_from_biblio_note("MS # B-810, Schmidt.", url)
        assert r and r["biblio_category"] == "archival" and "OCMH" in r["holding"]


# --- Generated per-volume biblio-URL data file (handles -Biblio/-Bib/-B naming) ---
def test_generated_biblio_url_used_for_nonstandard_naming():
    # Guadalcanal's note is USA-P-Guadalcanal-Bib.html (NOT -Biblio.html); the data file must
    # drive the correct URL rather than the guessed -Biblio.html.
    url = "https://www.ibiblio.org/hyperwar/USA/USA-P-Guadalcanal/USA-P-Guadalcanal-5.html#fn1"
    r = resolve_from_biblio_note("Interv with a participant.", url)
    assert r and r["biblio_note_url"].endswith("USA-P-Guadalcanal-Bib.html")


def test_pacific_volume_archival_category_resolves():
    # A Pacific volume with no hand-curated published works still resolves archival citations
    # via the generic category layer + gets a correct biblio URL.
    url = (
        "https://www.ibiblio.org/hyperwar/USA/USA-P-Marianas/USA-P-Marianas-7.html#fn2"
    )
    r = resolve_from_biblio_note("25th Div G-3 Journal, 15 Jun 44.", url)
    assert r and r["biblio_category"] == "archival"
    assert "USA-P-Marianas" in r["biblio_note_url"]
