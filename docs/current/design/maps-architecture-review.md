# Maps Architecture Review (all phases)

**Status:** REVIEW · 2026-11-02 · grounded in a code read of `src/extraction/*map*.py`,
`phase{1,2,3}_*.py`, `src/schemas/maps_output.py`, `src/json_schemas.py`.
**Purpose:** document how maps flow through the pipeline today, where the paths overlap/conflict,
where Grok is (and isn't) involved, and the open questions — BEFORE changing the maps code.

---

## 1. TL;DR findings

1. **There are TWO independent in-document map paths**, in different phases, doing nearly the same
   job — a **redundancy/conflict** that must be resolved:
   - **Phase 2** `src/extraction/maps.py` (`extract_maps`) — deterministic text-regex finder +
     download + keyword-classify + persist. **Currently finds 0** (its regex matches
     `[Map X](http://...)`, which the real source doesn't use).
   - **Phase 3** `src/extraction/map_link_source.py` (`ingest_in_doc_map_links`, "multimodal gap
     M1") — consumes the parsed `maps[]` array directly (`[Map VIII](maps/*.jpg)` author-curated
     links) and ingests each as a *map-as-source-document*. This is the NEWER path and reads the
     parsed data rather than re-regexing text.
2. **THREE deterministic map finders exist, and ALL THREE find 0 on HyperWar** — because each is
   keyed on markdown `[Map X](...)` syntax that the source does NOT use (its maps are raw-HTML
   `<a href><img>` after pandoc):
   - `src/parser.py:extract_maps` (Phase 1) → populates `doc.maps[]`. Matches `[Map X](target)`
     (absolute OR relative). **Produces empty `maps[]`** (verified: ch-8 `maps[]`=0 in every
     sub-section).
   - `src/extraction/maps.py:_extract_maps_from_text` (Phase 2) → matches `[Map X](http://...)`.
     **0.**
   - Phase 3 `map_link_source` READS `doc.maps[]` → since that is empty, it also ingests **0**.
   The maps ONLY exist in the parsed `images[]` array, captured by the #672 anchor extractor (the
   only finder that matches the real raw-HTML `<a href="maps/..">` form). Verified ch-8: `images[]`
   carries the 11 hi-res maps; `maps[]` is empty.
3. **Grok vision IS used — but only in the EXTERNAL/web-search path**, not the source-document
   path. `grok_search_maps.verify_map_with_vision` vision-verifies web-found maps; it caches under
   `cache_type="vision_verification"`. The deterministic source-document `maps.py` has NO Grok call.
4. **Map type classification is deterministic keyword matching** (`_classify_map_type`), not LLM.
5. **8 map modules** exist with overlapping responsibilities (§3) — the architecture has accreted;
   a consolidation pass is warranted.

---

## 2. Per-phase flow (as built)

### Phase 0 — Ingestion (HTML → markdown)
- `src/ingestion/text_converters.py:html_to_markdown` strips boilerplate + runs **pandoc**.
- Pandoc PRESERVES map references but often as **raw inline HTML** (`<a href="maps/-N.jpg">
  <img src="maps/-Nm.jpg"></a>` and link-only `<a href="maps/-XII.jpg">Map XII</a>`).
- No map-specific logic here; maps ride along as generic image/anchor HTML.

### Phase 1 — Parse (markdown → parsed JSON)
- `src/parser.py` builds `doc.images[]` and `doc.maps[]`.
- `phase1_parse.py:52` serialises `maps[]` = `[{url, description, map_id}, ...]`.
- `src/parser.py:extract_maps` populates `doc.maps[]` from markdown `[Map X](...)` — EMPTY on
  HyperWar (raw-HTML form, not markdown).
- **#672 fix:** `_extract_html_anchor_images` captures the raw-HTML `<a href><img>` map/figure
  anchors into `images[]` at **hi-res** (click-through `-N.jpg`, not the `-Nm` thumbnail). This is
  currently the ONLY populated channel for ch-8 maps (`maps[]`=0, `images[]` has all 11).

### Phase 2 — Extract (`maps.py:extract_maps`)
- Gated on `config.maps.enabled`. `phase2_extract.py:_extract_maps`.
- **Finds** maps via `_extract_maps_from_text(all_text)` over each sub-event's `Sub-event_fulltext`
  — regex `\[Map\s+([^\]]+)\]\((https?://[^\)]+)\)` → **0 matches on HyperWar** (the bug).
- If found, per map: `_download_map_image` (requests) → `_classify_map_type` (keyword vocab) →
  `_create_map_record` (rich: MapID + Event/Sub-event/Place/Date links + file_format + url) →
  `_save_map_record` + index + processed-events registry (dedup).
- Also Phase 2 imports a LOCAL curated corpus via `external_maps.import_maps`
  (`external_maps.yaml`) — distinct from web search.
- **Intended behavior:** every narrative-referenced map → downloadable, typed, event/place/date-
  linked Map entity. Sound design; only the finder is wrong for the source format.

### Phase 3 — Enrich (web search + in-doc recycle)
- `phase3_enrich_data.py` runs three map-related steps (NAT/OpenSERP up exactly once here):
  1. **OpenSERP external maps** (`openserp_maps.import_openserp_maps`) — real search engines
     (Google/Bing/DDG) find place maps on the web.
  2. **Grok search maps** (`grok_search_maps`) — Grok search on whitelisted sites + **Grok vision
     verification** (`verify_map_with_vision`, cached `vision_verification`).
  3. **In-doc map links** (`_ingest_in_doc_map_links` → `map_link_source.ingest_in_doc_map_links`)
     — recycles the parsed `maps[]` author-curated links as map-as-source. **This overlaps Phase-2
     `maps.py`.**
- `map_source_adapter` renders a vision-approved map into the `parsed.json` contract (map-as-source
  document, so a map's own content can be extracted).
- `map_registration` — reverse-links narrative entities onto a map's coverage extent
  (covered_places × date_range).

---

## 3. Module inventory (8)

| Module | Phase | Role | Grok? |
|---|---|---|---|
| `maps.py` | 2 | source-text map finder + download + keyword-classify + persist | no |
| `external_maps.py` | 2 | import LOCAL curated corpus (`external_maps.yaml`) | no |
| `openserp_maps.py` | 3 | web search for place maps (OpenSERP) | no (search engines) |
| `grok_search_maps.py` | 3 | Grok search on whitelisted sites + **Grok vision verify** | **yes (search+vision)** |
| `search_external_maps.py` | (lib) | Grok-AI external map search workflow | yes |
| `map_link_source.py` | 3 | in-doc `maps[]` link ingestion (map-as-source, "M1") | via adapter |
| `map_source_adapter.py` | 3 | render a vision-approved map into `parsed.json` | via vision |
| `map_registration.py` | 3 | reverse map↔entity extent linking | no |

Schemas: `src/schemas/maps_output.py` (the Map entity: MapID + title/type/source/url/local paths +
Event/Sub-event/Place/Date links + figure/page number) and `map_features_output.py` (features on a
map). Legacy `MAP_SCHEMA` also in `src/json_schemas.py`.

---

## 4. Where Grok is involved (for the "don't resubmit in Phase 3" TODO)

- **Only** `grok_search_maps.verify_map_with_vision` (+ `map_source_adapter` vision approval) calls
  Grok on map IMAGES. It caches under `cache_type="vision_verification"` and
  `grok_client.extract_json_with_image_base64`.
- **Risk (TODO):** a Phase-3 re-run over an already-processed book must NOT re-vision the same
  images. Needs: (a) the vision verdict persisted on the record + a `*_checked_at` gate, (b) the
  cache keyed on image identity (stable), (c) an OBSERV asserting 0 new vision calls on a clean
  re-run. CURRENT caching is `vision_verification` cache_type — verify it is image-identity-keyed
  and survives reindex (the equipment cache-key lesson: key on stable identity, not prompt hash).
- The deterministic source-document path (`maps.py` / `map_link_source.py`) does NOT vision-submit
  the author-curated maps — they are known-good (in `/maps/`), so no Grok is needed for them.

---

## 5. The core defect + the #672 foundation

- **Defect:** Phase-2 `_extract_maps_from_text` regex never matches HyperWar's forms → 0 maps, and
  the whole download/classify/link/persist pipeline never runs.
- **#672 (merged):** the Phase-1 parser now captures all 11 chapter-8 maps (hi-res) into `images[]`.
- **So the maps are now AVAILABLE in the parsed data** — the fix is to feed the map pipeline from
  the parsed map URLs (deterministic, no Grok for in-doc maps), not the broken text regex.

---

## 6. Open questions (resolve before building)

- **O1 — THREE starved finders; maps only in `images[]`:** `parser.extract_maps`→`maps[]` (empty),
  `maps.py` text-regex (0), and Phase-3 `map_link_source` (reads the empty `maps[]` → 0) are ALL
  keyed on markdown `[Map X](...)`. Post-#672 the maps live ONLY in `images[]` (`/maps/` anchors).
  DECISION NEEDED: make `images[]` (URL/label-classified) the single source of truth and route the
  (one) in-doc map ingester from it; retire the dead markdown-`[Map X]` finders OR extend
  `parser.extract_maps` to also read the anchor form so `maps[]` is populated. Recommend the latter:
  have the parser put maps in `maps[]` (one authoritative channel) via the anchor logic.
- **O2 — Phase 2 vs Phase 3 in-doc maps:** two paths ingest the same author-curated maps
  (`maps.py` @P2, `map_link_source` @P3). This is duplication and a likely double-persist/dedup
  hazard. Decide which phase owns in-doc maps and RETIRE the other (recommend: one path).
- **O3 — classification:** keyword `_classify_map_type` vs the tiered Grok-vision backstop we
  discussed. For in-doc `/maps/` maps, deterministic is sufficient (they're known maps). The
  Grok-vision backstop is only for AMBIGUOUS images on sources without a clean maps convention.
- **O4 — cross-source generalization:** `/maps/` path convention is ibiblio-specific. The
  generalizable map signal is the caption/label/prose "Map N" + the Grok-vision backstop for the
  questionable residual (tiered: deterministic map-word → not-a-map → Grok vision).
- **O5 — idempotency (the TODO):** ensure Phase-3 vision verification is not re-run per re-run.

---

## 7. Recommendation (direction, not yet built)

1. **Pick one source of truth** for in-doc maps (parsed `images[]`/`maps[]`, URL+label classified)
   and **one phase** that owns ingesting them; retire the duplicate path (O1/O2).
2. **Feed the existing deterministic pipeline** (download → classify → Event/Place/Date link →
   persist) from that source — this alone takes chapter-8 maps 0 → 11, no Grok.
3. **Add the tiered classifier** (deterministic map-word/label → not-a-map → **Grok-vision backstop
   for the questionable residual**, batched + cached/idempotent per O5) for cross-source robustness.
4. **Keep the web-search + vision modules** (`openserp_maps`, `grok_search_maps`) as the SEPARATE
   "no map in source → find one on the web" feature — do not conflate with source-document maps.
