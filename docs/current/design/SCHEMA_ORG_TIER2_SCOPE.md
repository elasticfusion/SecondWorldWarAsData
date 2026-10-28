# Tier-2 schema.org population — SCOPE (geocoder/source "capture more")

**Status:** SCOPE ONLY. No code written. This plans the Tier-2 orphan-field population deferred by
the Tier-1 work (see `SCHEMA_ORG_REVIEW.md` §11). Tier-1 populated fields from data the pipeline
already fetched then discarded (image dims, `contentLocation`, `sameAs` from the Wikipedia URL we
already resolve). **Tier-2 = fields that require CAPTURING data we don't currently request.**

Grounded in the actual code (file:line) so it's directly actionable.

---

## 0. The three Tier-2 units, ranked by cost

| Unit | Field(s) | New capture needed? | Cost |
|---|---|---|---|
| **T2-A Wikidata QID** | `places.sameAs` (+ a QID) | **No new request** — piggyback on the EXISTING Wikipedia call | ⭐ cheapest |
| **T2-B elevation** | `places.elevation_m` | **New source** (elevation API) + cache | ◆ medium |
| **T2-C gazetteer id** | `places.sameAs` (GeoNames/OSM) | New capture via Nominatim params OR a GeoNames call | ◆ medium |

Do **T2-A first** — the finding below makes it nearly free.

---

## 1. T2-A — Wikidata QID into `places.sameAs`  ⭐ ✅ IMPLEMENTED (2026-10-27)

**Finding (important):** the places Wikipedia enrichment ALREADY calls the MediaWiki API with
`prop=extracts|pageimages|categories` (`src/extraction/enrich_places.py:112` `_fetch_place_wikipedia_full`,
via the SSRF-safe, cached, provenance-recorded `recorded_fetch`). Adding **`pageprops` to the `prop`
list + `ppprop=wikibase_item`** returns the article's **Wikidata QID in the SAME response** — no new
request, no new rate-limit surface.

- **Integration point:** `_fetch_place_wikipedia_full` params (enrich_places.py:131) — add
  `"pageprops"` to `prop` and `"ppprop": "wikibase_item"`. Parse `pages[].pageprops.wikibase_item`
  (e.g. `Q181943`) in `_build_wiki_place_result` (enrich_places.py:81).
- **Write:** turn the QID into a canonical URL `https://www.wikidata.org/wiki/Q181943` and feed it
  through the EXISTING `_add_place_sameas` (enrich_places.py:31) — it already dedups into `sameAs[]`.
  (Optionally also store the bare QID in `identifier[]` if/when that field is adopted — see
  SCHEMA_ORG_REVIEW.md §1/§9 recs 2.)
- **Provenance:** the QID comes from the article we already resolved — same trust level as
  `wikipedia_url`. Confident-only; absent `pageprops` → no QID (null-over-guess).
- **Reuse:** `recorded_fetch` (SSRF-safe + cached + fetch-provenance), `_add_place_sameas`,
  `url_registry` 90-day dedup — all already in that function.
- **Risk/caveat:** a Wikipedia article may legitimately have no Wikidata item (rare) → skip. The QID
  is MODERN identity — same modern-identity-cross-walk caveat as all place `sameAs` (keep
  `historical_names` authoritative).
- **Schema:** none — `places.sameAs` already exists (2.30). Pure population.
- **Tests:** stub the MediaWiki response with/without `pageprops.wikibase_item`; assert the QID URL
  lands in `sameAs[]`, deduped, and absent-QID → no change. (+ the existing _add_place_sameas tests.)
- **Effort:** small — ~1 param change + 1 parse + reuse the writer.

---

## 2. T2-B — `places.elevation_m` (new source)  ◆

The geocoder's `GeocodeResult` (`src/enrichment/places_grok_geocode.py:84`) carries ONLY
`latitude/longitude/country/confidence/source` — **no elevation**. So elevation is a genuine new
capture: given the coords we already have, call an elevation API.

- **Source options:** Open-Elevation (free, no key), USGS EPQS (US only), or Open-Meteo elevation.
  Open-Elevation is the simplest global default. One GET per geocoded place: `?locations=lat,lon` →
  `{results:[{elevation}]}`.
- **Integration point:** a new step AFTER a successful geocode, in `_apply_result`
  (places_grok_geocode.py:561) or a sibling pass in `_enrich_places_and_geocode`
  (phase3_enrich_data.py:145) — gated on a confident `coordinates` existing (don't look up
  elevation for an ungeocoded place).
- **Must reuse / add (NON-NEGOTIABLE for a new external call):**
  - SSRF-safe fetch: `url_hygiene.safe_get` / `fetch_text_safe` (url_hygiene.py:371/448) — the
    elevation host is fixed/allowlistable, but still go through the safe fetcher.
  - Caching: per-coord cache like the Nominatim geocoder's `_cache_path` (nominatim_geocode.py:143)
    under `args.cache_dir / "elevation"` — elevation is immutable, so cache forever.
  - fetch-provenance: `recorded_fetch` provider `"open-elevation"` (kind structured, provenance only).
  - Rate-limit / fail-safe: Open-Elevation is courtesy-rate-limited → batch coords where the API
    allows, back off, and NEVER block the geocode on an elevation miss (null-over-guess).
- **Gate:** a config flag `places.capture_elevation` (default off until we're happy with the source),
  so it's opt-in.
- **Provenance nuance:** store `elevation_m` as the MODERN terrain elevation at those coords — fine
  for a hill/height, but note it's modern DEM data, not a wartime-stated figure.
- **Schema:** none — `elevation_m` already exists (2.30). Pure population.
- **Tests:** stub the elevation API (hit/miss/error); assert `elevation_m` set on a geocoded place,
  null on miss, cache hit avoids a second call, fail-safe on API error (no crash, geocode unaffected).
- **Effort:** medium — new fetch helper + cache + provenance + gate + tests. One new (free, keyless)
  external dependency — flag to the owner before enabling by default.

---

## 3. T2-C — GeoNames / OSM id into `places.sameAs`  ◆ (OSM piggyback ✅ IMPLEMENTED 2026-10-27; GeoNames still future)

A second gazetteer id strengthens dedup (the open place-dedup-by-coords issue) beyond Wikidata.

- **Two capture paths:**
  1. **Nominatim piggyback (cheaper):** the Nominatim geocoder already calls the OSM API
     (`nominatim_geocode.py:161` params). Nominatim returns `osm_type`+`osm_id` and, with
     `extratags=1`/`namedetails=1`, sometimes a `wikidata`/`wikipedia` tag. Capture `osm_id` →
     `https://www.openstreetmap.org/<type>/<id>` into `sameAs[]`. BUT: Nominatim is only ONE rung of
     the cascade (Grok geocoder is primary), so this only fires when Nominatim won — partial coverage.
  2. **GeoNames (fuller, new call):** a GeoNames `search`/`findNearbyPlaceName` by name+coords →
     `geonameId`. Needs a free GeoNames username (registration) + its own cache + rate limit. More
     setup; better coverage + the canonical gazetteer id for the dedup fix.
- **Integration point:** Nominatim path = extend `nominatim_geocode.py` result capture + thread into
  `GeocodeResult` (add an optional `external_ids` field) → `_apply_result` writes them to `sameAs[]`.
  GeoNames path = a new enrichment step like T2-B.
- **Reuse:** same SSRF-safe fetch + cache + provenance + `_add_place_sameas` discipline as T2-A/B.
- **Schema:** `places.sameAs` exists. If we also want the bare id, that's the `identifier[]` adoption
  (SCHEMA_ORG_REVIEW.md rec 2) — a small additive schema change (its own version bump).
- **Decision needed:** OSM-piggyback (cheap, partial) now, GeoNames (fuller, needs a username) later?
- **Effort:** OSM piggyback = small-medium; GeoNames = medium + an external account.

---

## 4. Cross-cutting (applies to all three)

- **All deterministic, no LLM.** These are identifier/measurement captures, not text extraction —
  consistent with the §11 principle (do NOT prompt for these).
- **Fail-safe + null-over-guess:** a capture miss/error leaves the field null and never blocks the
  geocode/enrichment pass.
- **Cache everything:** identifiers + elevation are immutable; cache permanently to avoid re-fetching
  across runs (reuse the per-name/per-coord cache pattern already in the geocoders).
- **Provenance:** every new external call goes through `recorded_fetch` with a named provider so it's
  traceable, and honors `url_registry` dedup where applicable.
- **Observability:** `OBSERV place.sameas_wikidata` / `place.elevation` / `place.sameas_osm` with the
  id/value, mirroring the Tier-1 OBSERV lines.
- **Each unit ships as its OWN PR** (gate → dual review → merge), smallest first (T2-A).

## 5. Recommended order
1. **T2-A (Wikidata QID)** — nearly free (piggyback), biggest dedup/interoperability payoff.
2. **T2-C OSM-piggyback** — free-ish (Nominatim already called), partial coverage.
3. **T2-B elevation** — new keyless API, opt-in flag, owner sign-off before default-on.
4. **T2-C GeoNames** — only if the dedup fix wants the canonical gazetteer id (needs an account).
