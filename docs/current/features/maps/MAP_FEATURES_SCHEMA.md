# Map Features Schema (design — for review)

**Status:** ENFORCED — `src/schemas/map_features_output.py` (strict, `additionalProperties:
False`), validated by `tests/test_map_features_schema.py` and self-checked by
`scripts/proto_map_vision.py` before write. This doc is the rationale; the module is the
source of truth.
**Purpose:** Hold the *interior* of a historical map (units, places, boundaries, routes,
fortifications) extracted by Grok vision, as a **unified, deduplicated** feature set that
(a) links to our entity graph (PlaceID / GroupID / DateID), (b) carries provenance +
confidence, and (c) renders directly on an OpenStreetMap/Leaflet/Mapbox client.

---

## Design basis: adapt, don't invent

We base this on three existing standards and extend them to our needs:

- **GeoJSON** (RFC 7946) — the container. `FeatureCollection` of `Feature`s with
  `geometry` (`Point` / `LineString` / `Polygon`). Any GIS/OSM client reads it as-is.
- **TacticalJSON** (spatialillusions) — GeoJSON profile for military overlays; property
  names mirror **MIL-STD-2525D / NATO APP-6**. Its spec explicitly says *"any other
  properties can be added"* — so our extensions stay spec-compliant and remain renderable
  by `milsymbol` / `orbat-mapper`.
- **2525/APP-6 text modifiers** — canonical field names reused verbatim where they fit:
  `sidc`, `uniqueDesignation`, `higherFormation`, `additionalInformation`, `dtg`.

What is **novel** (not in any precedent) is extracting this from a *raster map image*; the
schema is the well-trodden part.

---

## The honest coordinate model (load-bearing)

Grok vision reads **printed labels**, not pixel-georeferenced geometry. Therefore:

- Coordinates come from the **resolved PlaceID's gazetteer location** (OSM Nominatim →
  Grok cascade), NEVER from measuring the symbol's pixels.
- Point features (towns, named features) geolocate precisely. Unit positions / arrows /
  boundaries inherit coordinates from their **named anchors** (approximate, by design).
- Every geometry records `coordinate_source` ∈
  `{placeid_gazetteer, anchor_place, none}` — **never** `map_pixels`.
- `coordinates` are `null` until the anchor Place is geocoded. Null over fake. (Mirrors the
  weather `null`-coord convention.)

---

## Shape

```json
{
  "type": "FeatureCollection",
  "MapID": "01ULID...",
  "map_number": "MAP III",
  "map_title": "The Ardennes: German Penetration, 16-19 December 1944",
  "legend": [
    {
      "symbol_description": "solid red line",
      "meaning": "front line, end of 16 December",
      "DateID": "01ULID...",
      "confidence": 0.8
    }
  ],
  "scale_text": "SCALE 1:... / verbatim scale-bar text if printed",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [6.33, 50.25] },
      "properties": {
        "feature_kind": "unit_position",

        /* --- provenance (source-is-authority) --- */
        "original_label": "1 SS Pz",
        "source": "map",
        "MapID": "01ULID...",
        "confidence": 0.62,
        "legend_key": "solid red line",
        "tile_id": "q2",
        "coordinate_source": "anchor_place",

        /* --- entity-graph links (null if unresolved; never guessed) --- */
        "PlaceID": "01ULID...",
        "GroupID": "01ULID...",
        "PersonID": null,
        "DateID": "01ULID...",

        /* --- 2525/APP-6 text modifiers (verbatim reuse) --- */
        "sidc": "SHGPUCAA-------",
        "uniqueDesignation": "1 SS Pz",
        "higherFormation": "I SS Pz Corps",
        "additionalInformation": null,
        "dtg": "1944-12-16",

        /* --- derived tactical semantics (2525/APP-6 aligned) --- */
        "affiliation": "hostile",
        "echelon": "division",
        "posture": "attack"
      }
    }
  ]
}
```

### `feature_kind` enum
`place | unit_position | unit_boundary | route | fortification | river | road | railroad |
elevation | front_line`

- `place` → `Point` → resolves to **PlaceID** (Tier-1).
- `unit_position` → `Point` → **GroupID** + anchor **PlaceID** + posture/affiliation/echelon.
- `unit_boundary` / `front_line` → `LineString` → anchored by a sequence of named places.
- `route` → `LineString` → **GroupID**, ordered named-place anchors, `dtg`/DateID (Tier-2).
- `fortification` (West Wall) / `river` / `road` / `railroad` → `Point`/`LineString`.

### Field groups
| Group | Fields | Notes |
|---|---|---|
| Provenance | `original_label`, `source="map"`, `MapID`, `confidence`, `legend_key`, `tile_id`, `coordinate_source` | source is authority; verbatim label always kept |
| Entity links | `PlaceID`, `GroupID`, `PersonID`, `DateID` | resolved via shared libs; `null` when below confidence floor |
| 2525/APP-6 modifiers | `sidc`, `uniqueDesignation`, `higherFormation`, `additionalInformation`, `dtg` | names reused verbatim from the standard |
| Tactical semantics | `affiliation` (friend/hostile/neutral/unknown), `echelon`, `posture` (attack/defend/axis) | 2525/APP-6 aligned enums; `posture` is Tier-2 |

---

## Why this satisfies the requirements

- **Unified result set** — tiles are an internal recall mechanism; cross-tile duplicates
  collapse on the resolved entity (PlaceID/GroupID) with `tile_id` + position as tiebreaker
  for same-name/different-location (the multi-"Roth" case). Output is one FeatureCollection.
- **places + people_groups integration** — every feature resolves to PlaceID/GroupID as a
  map-sourced *mention* on the shared graph; the Roth-16-Dec cross-source join is a
  (PlaceID, DateID) lookup.
- **OSM overlay** — it already *is* GeoJSON; coordinates come from the resolved Place via
  the existing OSM-Nominatim/Grok geocode cascade. No pixel georeferencing.
- **Interoperable** — valid GeoJSON + TacticalJSON ⇒ consumable by `milsymbol`,
  `orbat-mapper`, QGIS, Leaflet/Mapbox without translation.
- **Provenance / non-redundancy** — `source:"map"` + `original_text`-equivalent
  (`original_label`) keep map facts distinct from text facts (same discipline as the
  weather narrative-vs-NOAA split).

---

## Open questions before building the enforced schema

1. Confirm the `feature_kind` enum covers the Green Book series (anything to add/drop?).
2. Confidence floor for auto-resolving PlaceID/GroupID (suggest reuse the weather/equipment
   matcher thresholds — ~0.88 name match) before falling back to `null` + verbatim label.
3. Do we store the FeatureCollection on the existing map record (new `map_features` key) or
   as a sibling `output/map_features/<MapID>.json`? (Leaning sibling — keeps the catalog
   record small and lets the overlay layer be regenerated independently.)

---

## Measured results — live Tier-1 run on Map III (`scripts/proto_map_vision.py`)

Global legend pass + 2x2 overlapping tiles + merge + resolve-then-dedup, against the live
corpus (3457 places, ~4035 people_groups name-forms):

| Layer | Result |
|---|---|
| map_number / title / legend | 100% — "MAP III", full title, all 5 dated legend entries |
| places extracted | 72 (deduped from 83 across tiles) |
| **places resolved → PlaceID** | **47/72 (65%)**; 25 null (verbatim kept, not guessed) |
| units extracted | 28 (deduped), affiliation ~100% correct, echelon mostly correct |
| **units resolved → GroupID** | **0/28** — see finding below |

**Key finding — unit nomenclature mismatch (not a logic bug).** The entities exist, but the
map uses tactical abbreviations while people_groups uses doctrinal/narrative names:

| Map label | people_groups entity |
|---|---|
| `18 VG` | `18th Volksgrenadier Division` |
| `423 INF` | `423d Infantry` / `423d Regiment` |
| `7 AD` | `7th Armored` |
| `CCA 7` | `CC-A, 7th Armored Division` |
| `106` | `106th Inf Div` |

An exact lookup can't bridge these. **Units need a map-abbreviation expander**
(`VG→Volksgrenadier Division`, `AD→Armored Division`, `CC→Combat Command`, `INF→Infantry`,
ordinal `106→106th`), directly analogous to the existing `equipment_disambiguation` curated
+ learned alias stores. Places integrate well today; units are blocked on this translation
layer. This is the primary thing the prototype surfaced.

**Other rough edges:** dense-cluster OCR noise on a few labels (`106 XX 28`, `II30 560.VG`)
— the division symbol (`XX`) bleeding into text; a cleanup/normalization pass + resolution
confidence floor handle these.

---

## Tier-2 probe — dated movement layer (one tile, live)

Given the dated legend, Grok extracted MOVEMENT as structured `unit × from→to × date ×
posture` relations and dated front lines:

- **10 movements** on one tile, e.g.
  - `18 VG | hostile | attack | Ormont → Roth | 16-19 DEC`
  - `14 CAV | friend | defend | Roth → Kobscheid`
  - `1 SS | hostile | attack | Hallschlag → Losheim | 16-19 DEC`
  - `422 INF | friend | axis | Schonberg → Oberlascheid | 18-19 DEC`
- **3 dated front lines** with the named places each passes near (US positions 15 Dec vs 19
  Dec; West Wall anchored at Bleialf/Grosslangenfeld/Ormont).

**The `18 VG → Roth, 16-19 Dec` + `14 CAV defend Roth` lines are the flagship Roth-16-Dec
cross-source use-case answered directly from the map.** Tier-2 is substantially reachable;
gated mainly on (a) the unit-nomenclature expander (same blocker as Tier-1 units) and (b)
tile-seam merging for arrows whose origin is off-tile. Minor OCR artifacts (Hallschlag,
Weckerath) as expected.

**Conclusion:** Green Book maps yield Tier-1 strongly today and Tier-2 (dated operational
movement) with the unit-expander + seam-merge. Tier-3 (survey geometry) stays out by design
(coordinates via resolved PlaceID, never pixels).

---

## Reverse registration — map as a backdrop (`src/extraction/map_registration.py`)

The complement to forward extraction: a map advertises a coverage **extent** =
`covered_places` (resolved PlaceIDs) × `date_range` (from the dated legend). Any narrative
entity (person, casualty, logistics, equipment) resolved to a `(PlaceID, DateID)` INSIDE
that extent gets a back-link `maps:[{MapID, association: spatial_temporal_coverage}]` — even
though the map never names it. The join is the entity graph, so it is immune to map-OCR
fuzziness and needs no unit-nomenclature resolution.

- Extent is derived by `proto_map_vision.derive_extent` (Map III → 49 covered places,
  1944-12-15 .. 1944-12-19).
- `register_entity_to_maps(record, extents, dateid_to_iso)` tests place membership + date
  range; place-only maps (no range) match on place alone; a dated map requires an in-range
  date (null date → no link). Never mutates files (report mode).

**Measured (honest):** the mechanism is proven on the user's flagship example — "Sgt Smith,
DSC, St. Vith, 16 Dec 1944" registers to Map III; a 22-Dec date or an off-map place both
correctly produce no link. BUT running it against the current `output/` corpus yields **0
real registrations**: the ingested narrative (e.g. casualties around Le Mans / 9 Aug) is
from a different sector than Map III's Ardennes extent — there is simply no entity at those
places+dates yet. The pass will light up once Bulge-sector narrative is ingested. Validated
by 5 deterministic tests in `tests/test_map_registration.py`.

---

## Chandra vs. Grok (measured on the identical Map III) — recommendation

Both tools were run on the SAME Map III: Grok via the prototype, Chandra via the deployed
GPU pipeline (`wwii-chandra`, DPI 300, on-demand).

| | Chandra (GPU OCR) | Grok vision |
|---|---|---|
| Title + legend text | ✓ | ✓ |
| Elevation + distance scales | ✓ | ✓ *(after prompt fix — see below)* |
| Places | ~3, in prose | **74 enumerated** (47 → PlaceID) |
| Units | ~4, in prose | **27** with affiliation + echelon |
| Affiliation / dated movement | prose only ("red arrows = German attacks") | **structured** (`18 VG → Roth 16-19 Dec`) |
| Output form | caption/alt-text + webp crops | typed GeoJSON features |
| Infra cost | GPU Batch: NAT + 7 VPC endpoints + GPU instance (~10 min/page), mandatory teardown | one HTTPS call (key only) |

**The only thing Chandra captured that Grok initially missed — the elevation/distance
scales — was a prompt omission, not a capability gap.** After adding scales to the legend
prompt, Grok returns them verbatim:
`elevation_scale: "ELEVATIONS IN METERS 0 400 500 600 AND ABOVE"`,
`distance_scale: "0 1 2 3 MILES / 0 1 2 3 KILOMETERS"` — matching Chandra.

**Recommendation: Grok alone for the map interior.**
- Chandra adds no structured/relational data (architectural: it captions, it does not
  enumerate/relate), and its one incremental read (scales) is replicable in Grok for free.
- The tandem roughly doubles the moving parts (GPU pipeline + teardown) per map for a
  marginal, replicable gain.
- **Reserve Chandra for its real job** — prose pages and tables (Paddle/PP-StructureV3 for
  2-D tables), where it is the deployed, correct tool.
- **Revisit the tandem only for degraded/hand-drawn maps** where Grok's label OCR
  measurably fails (the prototype's "106 XX 28" noise is the signal). On clean Green Book
  maps that bottleneck did not appear. Tandem value scales with source degradation.
2. Confidence floor for auto-resolving PlaceID/GroupID (suggest reuse the weather/equipment
   matcher thresholds — ~0.88 name match) before falling back to `null` + verbatim label.
3. Do we store the FeatureCollection on the existing map record (new `map_features` key) or
   as a sibling `output/map_features/<MapID>.json`? (Leaning sibling — keeps the catalog
   record small and lets the overlay layer be regenerated independently.)
