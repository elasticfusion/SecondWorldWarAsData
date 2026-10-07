# Phase-3 External-Source Usage Report (Grokipedia / Wikipedia / OpenSERP / NOAA / NARA)

**Date:** 2026-10-06. Produced during the Phase-3 enrichment hardening. Lists every place the
pipeline still fetches from an external source, whether the fetch is **gated** (skips when
already-enriched/fresh) and **cached** (a re-call doesn't re-hit the network), the **write
path** (now version-stamped?), and a **refactoring flag**.

Legend: ✅ good · ⚠️ needs attention · 🔁 refactor candidate.

---

## Grokipedia

| Site | Gated? | Cached? | Notes / flag |
|---|---|---|---|
| `enrich_biographies.search_grokipedia` (people) | ✅ via `get_cached("grokipedia", name)` + entity `enrichment_gate`/`last_enrichment` | ✅ disk cache (`grokipedia` namespace, incl. negative cache) | Writes now stamped (people). 🔁 **Grokipedia is scraped via `grokipedia.com/search` HTML** — brittle (depends on page markup). Candidate to move behind a stable client or drop if Wikipedia suffices. |

Grokipedia is used **only** for people biographies. No other entity calls it.

## Wikipedia

| Site | Gated? | Cached? | Notes / flag |
|---|---|---|---|
| `enrich_biographies` (people: `_build_wikipedia_request`, `_search_wikipedia_fallback`, image) | ✅ cache + entity gate | ✅ via Grok cache / `_wikipedia_images` | Writes stamped (people). |
| `equipment_wikipedia.enrich_all_equipment_wikipedia` | ✅ **now gated** on `wikipedia_url` OR `wikipedia_checked_at` (this cycle) | ✅ `cache_result("wikipedia_equipment", …)` | Writes **now routed via `update_enriched`** (stamped + contract). Was raw write + no staleness gate. |
| `groups_wikipedia.enrich_all_groups_wikipedia` | ✅ **now gated** on `wikipedia_url` OR `wikipedia_checked_at` (this cycle) | ✅ `cache_result("wikipedia_group", …)` | Writes **now via `update_enriched`**. Same fix as equipment. |
| `equipment._extract_media_from_wikipedia` | partial (within equipment media flow) | via equipment media cache | 🔁 **A SECOND equipment↔Wikipedia path** separate from `equipment_wikipedia.py`. Two code paths hit Wikipedia for equipment — consolidate. |

## OpenSERP (image/academic search)

| Site | Gated? | Cached? | Flag |
|---|---|---|---|
| `openserp_enrichment` (people + equipment) | config-gated (`use_openserp`) + per-record | ✅ `cache_result("openserp_*", …)` | Writes stamped (people/equipment). Requires the `search_media` binary (absent in some envs). |

## NOAA / Nominatim / NARA-Archive.org (non-Grok/Wiki, for completeness)

| Site | Gated? | Cached? | Flag |
|---|---|---|---|
| `noaa_weather` | ✅ skip-if-`noaa_observed` + `{station}:{date}` cache | ✅ disk cache | OK (reviewed earlier). |
| `nominatim_geocode` / `places_grok_geocode` | ✅ geocode disk cache + `enrichment_gate` | ✅ | OK; policy-compliant (≤1 req/s, UA). |
| `bibliography_resolver` (NARA, Archive.org, Gutenberg, OpenSERP) | config-gated + per-record | ✅ | OK. |

---

## Refactoring candidates (flagged, not yet done)

1. 🔁 **Two equipment↔Wikipedia paths** — `enrichment/equipment_wikipedia.py` AND
   `extraction/equipment.py::_extract_media_from_wikipedia`. Consolidate into one gated path.
2. 🔁 **Grokipedia HTML scraping** (`search_grokipedia`) is markup-brittle — wrap in a stable
   client or evaluate dropping in favor of Wikipedia.
3. ⚠️ **Staleness vs. presence gating** — most gates are "already has the field" (presence),
   not time-based. A `*_checked_at` marker (added for equipment/groups Wikipedia this cycle)
   is the better pattern; roll it out to the people Grokipedia/Wikipedia path too so a
   not-found result isn't re-attempted every run.

## What was fixed this cycle
- equipment/groups Wikipedia: added `*_checked_at` gating (no redundant re-pulls) + routed
  writes through the native `enrich_write` path (version-stamped, contract-honored).
- All enrichment entity writes now stamp `_schema_version` via the entity-aware
  `inject_metadata` / `write_json_with_lock(entity=...)` (closed the silent-downgrade bug).
