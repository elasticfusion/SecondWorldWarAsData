"""Phase 2 performs NO supplemental searches (owner rule, 2026-11-07).

Phase 2 may only talk to Grokipedia/Wikipedia and submit data to Grok for extraction. Looking up where
a cited document can be obtained (NARA, archive.org, Gutenberg, OpenSERP, Grok "LLM search") is
Phase 3 (bibliography_resolver). XChannel-8 E2E: Phase 2 hit archive.org for every citation, ~87
Grok search + 87 Grok verify calls per chapter, and rejected nearly every hit; it could also pre-fill
`resource_urls`, which trips the Phase 3 resolver's "already resolved" short-circuit and blocks NARA.
"""

import ast
import inspect
import json
from pathlib import Path

import pytest
import yaml

import src.extraction.supplemental as sup
import src.extraction.supplemental_search as ss

# Phase 2 modules that must not reach any supplemental search source.
PHASE2_MODULES = ["src/extraction/supplemental.py", "src/extraction/bibliography.py"]
FORBIDDEN = {
    "sequential_search",
    "search_archive_org",
    "search_archive_org_candidates",
    "search_gutenberg_openserp",
    "search_gutenberg_candidates",
    "search_openserp",
    "search_llm",
    "supplemental_search",
}


def _names(path: str) -> set:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.update(n.module.split("."))
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.Import):
            names.update(a.name.split(".")[-1] for a in n.names)
    return names


@pytest.mark.parametrize("path", PHASE2_MODULES)
def test_phase2_modules_reference_no_search_source(path):
    assert not (_names(path) & FORBIDDEN), (path, _names(path) & FORBIDDEN)


def test_phase2_entrypoint_does_not_import_supplemental_search():
    assert not (_names("phase2_extract.py") & FORBIDDEN)


def test_phase2_chain_function_is_gone():
    assert not hasattr(ss, "sequential_search")
    assert not hasattr(sup, "_supplemental_url_verified")
    assert not hasattr(sup, "_enrich_material")


def _citation_material(**over):
    m = {
        "material_category": "referenced_material",
        "reference_type": "footnote",
        "content_class": "citation",
        "verbatim_reference": "Gordon A. Harrison, Cross-Channel Attack (Washington, 1951)",
        "citation": {"title": "Cross-Channel Attack", "author": ["Gordon A. Harrison"]},
        "resource_urls": [],
    }
    m.update(over)
    return m


def test_apply_license_makes_no_network_or_search_call(monkeypatch):
    """Every search function raises if called; license determination must not touch them."""

    def _boom(*_a, **_k):
        raise AssertionError("Phase 2 must not search")

    for name in (
        "search_archive_org",
        "search_gutenberg_openserp",
        "search_openserp",
        "search_llm",
        "search_archive_org_candidates",
        "search_gutenberg_candidates",
    ):
        monkeypatch.setattr(ss, name, _boom)
    import requests

    monkeypatch.setattr(requests.Session, "request", _boom)
    m = _citation_material()
    sup._apply_license(m)
    assert m["resource_urls"] == []  # never pre-filled in Phase 2
    assert m["license"]  # derived from the citation, no lookup


def test_route_one_material_leaves_resource_urls_empty_for_phase3(
    tmp_path, monkeypatch
):
    """The citation lands in the bibliography with an EMPTY resource_urls so the Phase 3 resolver
    (NARA first) is not short-circuited."""
    for name in ("search_archive_org", "search_llm", "search_openserp"):
        monkeypatch.setattr(
            ss, name, lambda *a, **k: pytest.fail("Phase 2 must not search")
        )
    stored = {}
    import src.extraction.bibliography as bib

    monkeypatch.setattr(
        bib, "store_bibliography_entry", lambda d, m, b, c: stored.update(m=m)
    )
    factual, ambiguous = [], []
    delta = sup._route_one_material(
        _citation_material(),
        "EV",
        "SE",
        "Sub",
        tmp_path,
        "B",
        "1",
        True,
        factual,
        ambiguous,
    )
    assert delta == 1 and stored["m"]["resource_urls"] == []


def test_enrich_flag_no_longer_exists_in_shipped_config():
    cfg = yaml.safe_load(open("config.yaml.example"))["supplemental_material"]
    assert "enrich_with_searches" not in cfg
    assert cfg["llm_search"] is True  # still configurable - now a PHASE 3 switch


def test_phase2_extract_does_not_read_a_search_toggle():
    src = Path("phase2_extract.py").read_text(encoding="utf-8")
    assert "enrich_with_searches" not in src


# --- Phase 3 owns the searches -------------------------------------------------------------


def _resolver():
    import src.enrichment.bibliography_resolver as br

    return br


def test_llm_search_is_a_phase3_source_and_must_verify(monkeypatch):
    br = _resolver()
    monkeypatch.setattr(ss, "search_llm", lambda t, a, g: "https://example.org/doc")
    monkeypatch.setattr(br, "_verify_url_content", lambda url, q, g: False)
    assert (
        br._find_in_llm({}, "q", "Title", "Auth", object(), {"llm_search": True}) == []
    )
    monkeypatch.setattr(br, "_verify_url_content", lambda url, q, g: True)
    assert br._find_in_llm(
        {}, "q", "Title", "Auth", object(), {"llm_search": True}
    ) == [("https://example.org/doc", "llm")]


def test_llm_search_respects_its_flag_and_needs_a_client(monkeypatch):
    br = _resolver()
    monkeypatch.setattr(
        ss, "search_llm", lambda *a: pytest.fail("must not call the LLM")
    )
    assert br._find_in_llm({}, "q", "T", "A", object(), {"llm_search": False}) == []
    assert br._find_in_llm({}, "q", "T", "A", None, {"llm_search": True}) == []


def test_llm_search_runs_only_when_no_other_source_found_a_copy(monkeypatch):
    """Cost: the LLM search (a Grok call + a verify call per citation) is the last resort."""
    br = _resolver()
    calls = []
    monkeypatch.setattr(br, "_find_in_nara", lambda *a, **k: [])
    monkeypatch.setattr(br, "_find_in_openserp", lambda *a, **k: [])
    monkeypatch.setattr(br, "_find_in_gutenberg", lambda *a, **k: [])
    monkeypatch.setattr(br, "_find_in_llm", lambda *a, **k: calls.append("llm") or [])
    monkeypatch.setattr(br, "_aggregate_findings", lambda e, f: None)

    monkeypatch.setattr(
        br, "_find_in_archive_org", lambda *a, **k: [("https://a/x", "archive_org")]
    )
    br._search_online_sources({}, "verbatim", "st", "T", object(), {})
    assert calls == []  # archive.org found one -> no LLM call

    monkeypatch.setattr(br, "_find_in_archive_org", lambda *a, **k: [])
    br._search_online_sources({}, "verbatim", "st", "T", object(), {})
    assert calls == ["llm"]


def test_resolve_config_is_built_once_and_carries_every_search_flag():
    import phase3_enrich_data as p3

    cfg = p3._bibliography_resolve_config(
        {
            "api": {"nara_api_key": "k"},
            "supplemental_material": {
                "llm_search": False,
                "search_gutenberg": False,
                "search_archive_org": True,
                "use_openserp": True,
            },
            "external_maps": {"openserp_url": "http://o:1"},
        }
    )
    assert cfg == {
        "nara_api_key": "k",
        "search_gutenberg": False,
        "search_archive_org": True,
        "llm_search": False,
        "use_openserp": True,
        "openserp_url": "http://o:1",
    }
    # DRY: both Phase 3 call sites use the helper (no second inline dict)
    src = inspect.getsource(p3)
    assert src.count("_bibliography_resolve_config(config)") == 2
    assert '"search_archive_org": supplemental_config.get' not in src


def test_untyped_citation_gets_the_full_verified_search_not_an_unverified_first_hit(
    monkeypatch,
):
    """The common green-book case ('Harrison, Cross-Channel Attack': untyped, not NARA) used to
    reach _resolve_generic -> archive.org FIRST hit, UNVERIFIED. It must now go through the same
    verified multi-source search (where the Phase-2 searches were moved)."""
    br = _resolver()
    seen = {}

    def _search(entry, verbatim, search_text, title, grok_client, config):
        seen["called"] = (verbatim, title)
        return ("https://www.gutenberg.org/ebooks/1", "gutenberg")

    monkeypatch.setattr(br, "_search_online_sources", _search)
    entry = {
        "verbatim_reference": "Harrison, Cross-Channel Attack, p. 12.",
        "citation": {"title": "Cross-Channel Attack", "author": ["Harrison"]},
    }
    out = br._resolve_generic_entry(entry, entry["citation"], object(), {})
    assert out == ("https://www.gutenberg.org/ebooks/1", "gutenberg", None)
    assert seen["called"][0].startswith("Harrison")
    assert not hasattr(br, "_resolve_generic")  # the unverified path is gone


def test_book_path_falls_back_to_verified_llm_search_once(monkeypatch):
    """The LLM fallback runs ONCE per book entry, after the title + alt_title searches (not inside
    each search, which would pay for it twice)."""
    br = _resolver()
    monkeypatch.setattr(ss, "search_archive_org", lambda *a, **k: None)
    monkeypatch.setattr(ss, "search_gutenberg_openserp", lambda *a, **k: None)
    monkeypatch.setattr(ss, "search_openserp", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(
        br,
        "_find_in_llm",
        lambda *a, **k: calls.append(1) or [("https://example.org/b", "llm")],
    )
    cit = {"title": "T", "alt_title": "T expanded", "author": ["A"]}
    got = br._resolve_book({}, cit, object(), {"use_openserp": True})
    assert got == ("https://example.org/b", "llm", None) and calls == [1]


def test_resource_urls_are_not_duplicated():
    br = _resolver()
    entry = {"resource_urls": ["https://a/x"]}
    br._apply_resolved_url(entry, "https://a/x", "archive_org", None)
    assert entry["resource_urls"] == ["https://a/x"]


def test_license_is_rederived_once_phase3_finds_a_url():
    """Phase 2 derives the license with empty resource_urls; a Gutenberg copy found in Phase 3
    must flip 'unknown' to public domain (the URL-based rule moved with the search)."""
    br = _resolver()
    entry = {"license": "unknown", "citation": {"title": "T"}}
    br._apply_resolved_url(
        entry, "https://www.gutenberg.org/ebooks/9", "gutenberg", None
    )
    assert entry["license"] == "public_domain"
    decided = {"license": "copyrighted", "citation": {}}
    br._apply_resolved_url(
        decided, "https://www.gutenberg.org/ebooks/9", "gutenberg", None
    )
    assert decided["license"] == "copyrighted"  # never overrides a decided license


def test_llm_search_is_deferred_in_batch_collect_pass(monkeypatch):
    """Batch COLLECT only queues Grok calls: running the LLM search would be swallowed as 'no URL'
    and raise a false verify alert. It must be skipped then and run on the batch re-run.
    """
    br = _resolver()
    monkeypatch.setattr(
        ss, "search_llm", lambda *a: pytest.fail("no LLM call in collect")
    )

    class _Batch:
        batch_mode = True

    assert br._find_in_llm({}, "q", "T", "A", _Batch(), {"llm_search": True}) == []


def test_llm_search_failure_is_visible(monkeypatch, caplog):
    import logging

    class _Boom:
        def chat_completion(self, **_k):
            raise RuntimeError("grok down")

    with caplog.at_level(logging.WARNING):
        assert ss.search_llm("T", "A", _Boom()) is None
    assert "bib.llm_search_failed" in caplog.text


def test_authored_citation_is_written_through_the_real_schema(tmp_path):
    """REGRESSION guard (review round 2): anything Phase 2 adds to a citation must be accepted by
    the bibliography schema, or the guarded write drops the record silently. Runs an AUTHORED
    material through the real routing + store path (no store mock)."""
    import json as _json

    from src.utils.file_lock import (
        write_json_with_lock,
    )  # noqa: F401 (real guard is used)

    material = _citation_material()
    factual, ambiguous = [], []
    bib = tmp_path / "bibliography"
    delta = sup._route_one_material(
        material, "", "", "", bib, "B", "1", True, factual, ambiguous
    )
    assert delta == 1
    recs = [
        _json.loads(p.read_text()) for p in bib.glob("*.json") if p.name != "index.json"
    ]
    assert recs, "authored citation was dropped by the schema guard"
    assert recs[0]["citation"].get("author") == ["Gordon A. Harrison"]
    assert "author_ids" not in recs[0]["citation"]
