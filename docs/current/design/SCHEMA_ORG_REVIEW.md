# schema.org review — additive enhancement SUGGESTIONS for our entity schemas

**Status:** SUGGESTIONS ONLY. No schemas were changed by this document. Each suggestion, if
adopted, must go through the normal evolve-+-archive discipline (`docs/current/SCHEMA_VERSIONING.md`:
bump the entity version → classify additive/breaking → re-pin the fingerprint → archive to
`src/schemas/history/` → Change Log row).

**Sources reviewed:** <https://schema.org/> (type vocabulary) and
<https://github.com/schemaorg/schemaorg> (the project / data model).

---

## 0. Framing: why schema.org fits — and where it does NOT

schema.org is a vocabulary for marking up web pages so **search engines** (Google, Bing, etc.)
can build a knowledge graph and rich results. That goal **overlaps** ours — we are also building a
queryable entity knowledge graph with typed entities (Person, Place, Event, Organization, Image,
CreativeWork/book) and cross-links — so its property names and linking patterns are a well-tested,
interoperable vocabulary we can borrow from selectively.

But it is **not the same problem**, and we should borrow, not adopt wholesale:

| schema.org (web/SEO) | Our corpus (WWII historical research) |
|---|---|
| Optimized for *discovery* + rich snippets; lax, publisher-friendly typing | Optimized for *provenance + queryable truth*; strict JSON-Schema + ULID identity + write-guard |
| Little notion of source confidence / qualification | Source qualification, confidence, `null-over-fake` are first-class |
| "Now"-centric; weak historical-date modeling (no fuzzy intervals, no pre-1 AD rigor) | Fuzzy date intervals, `resolved_earliest/latest`, precision are core |
| **"Now"-centric STATEFUL attributes** — a Person's `honorificPrefix`/`rank`/`hasOccupation`/`affiliation` describe the entity's *current* state (one value) | **Moment-in-time**: title/rank/role/affiliation are only true within a date interval, and a source's stated title is anchored to the SOURCE's date (General Marshall 1944 → Secretary of State Marshall 1947). Such attributes must be **time-bounded dated arrays with provenance**, never flat. |
| Identity by URL (`@id`, `sameAs` to Wikipedia/Wikidata) | Identity by internal ULID; external IDs would be *additive* cross-walk |
| Flat, open (`additionalProperty` escape hatch) | Closed (`additionalProperties:false`) + version-gated |
| No military domain (rank, OOB, command, campaign) | Military domain is the whole point |

**The single highest-value idea** to take from schema.org is **`sameAs`** — a list of authoritative
external IDs/URLs (Wikidata, Wikipedia, VIAF, GeoNames, DBpedia) on each entity. It is the
interoperability bridge: it lets our ULID-keyed graph round-trip to the global linked-data graph
without giving up our own identity model, and it pairs naturally with the ULID-attribution work.

Each section below: **schema.org type** → concrete **ADD** suggestions (additive, nullable) → the
**web-vs-us caveat** for that type. Priority tags: ⭐ high value, ◆ medium, ○ nice-to-have.

---

## 1. `people_output.py`  →  schema.org [`Person`](https://schema.org/Person)

Our schema is already rich (ranks, awards, units, group_affiliations, education, bio sources). Gaps
schema.org highlights:

> **DECISIONS (owner, 2026-10-27):** ✅ ADOPT `sameAs`, `identifier`,
> `givenName`/`familyName`/`additionalName`. ⚠️ `honorificPrefix`/`honorificSuffix`: do NOT adopt as flat
> fields — titles/ranks are MOMENT-IN-TIME (General Marshall 1944 → Secretary of State Marshall 1947);
> instead generalize the dated `ranks` pattern into a time-bounded `titles`/`honorifics` array
> ({title, kind, from, to, source_date, SourceSectionID}). ❌ DO **NOT** adopt `hasOccupation` (redundant
> with our `role_type`; its distinctive value — O*NET codes, civilian-career timelines — is low-value for
> a WWII event graph; if "roles over time" is ever wanted, make `role_type` a small `{role, from, to}` array in
> our own idiom instead). ❌ DO **NOT** adopt generic `relatedTo`/`knows`/`colleague` — a generic person-person edge
> array is O(n²) on dense command networks AND unsourced (co-mention ≠ "knew"); the relationships we need
> are already grounded via shared `event_mentions` + command structure (M6 / `group_affiliations` /
> `units_served`). If a specific need ever arises, revisit then as a NARROW source-attributed
> command-relation array (superior/subordinate + SourceSectionID + confidence, `null`-over-guess),
> reusing the M6 command-edge model — not schema.org's open relations.

- ⭐ **`sameAs`** (array of URL) — ✅ ADOPT. Authoritative external IDs for the person: Wikidata (`Q…`),
  VIAF, LCCN, Wikipedia, DBpedia. We already capture `wikipedia_url`/`grokipedia_url` inside
  `biographical_profile`; `sameAs` generalizes that to a first-class, multi-valued cross-walk and is
  the key to entity reconciliation across runs/corpora.
- ⭐ **`identifier`** (PropertyValue) — ✅ ADOPT. Typed external identifiers (service_number, VIAF,
  Wikidata QID) as `{propertyID, value}` (our own CLOSED sub-schema). More structured + matchable than
  stuffing them into `aliases`.
- ◆ **`honorificPrefix` / `honorificSuffix`** — ⚠️ REVISED DECISION (owner, 2026-10-27): do **NOT** adopt
  them as flat/static fields. schema.org types them as a single `Text` and is **"now"-focused** (the
  entity's *current* title for a search snippet). Our corpus is **moment-in-time focused**: a title/rank
  is only true within a date interval, and a source's stated title is anchored to the SOURCE's date.
  Example — **George Marshall** is "General Marshall" in a 1944 source but "Secretary of State Marshall"
  in a 1947 one; a single value (or an unordered array) is historically WRONG. INSTEAD: generalize the
  EXISTING dated-`ranks` pattern (`{rank, date, branch}`) into a time-bounded **`titles`/`honorifics`
  array** of `{title, kind: rank|office|honorific|post_nominal, from, to, source_date, SourceSectionID,
  confidence}` — military rank, civilian office, and honorific all handled uniformly, each with its
  validity interval + provenance, NEVER overwriting an earlier source-stated title with a later one. The
  top-level `rank` string stays only as a convenience denormalization; the dated array is the authority.
  Post-nominals ("VC, DSO, MC") are better DERIVED from `military_awards[]` than stored in parallel.
- ◆ **`givenName` / `familyName` / `additionalName`** — ✅ ADOPT. Structured name parts. We only store the
  full `name` + `aliases`; structured parts help the surname-matching the M3 linker already does by hand.
- ◆ **`hasOccupation`** (Occupation) — ❌ NOT ADOPTED. Redundant with `role_type`; schema.org's extras
  (occupational codes, civilian-career timelines) are SEO/labor-market oriented and low-value here. The
  military/command role-over-time is already better modeled by `units_served` + `ranks` +
  `group_affiliations` (dated, GroupID-linked).
- ○ **`nationality`** (already have) — schema.org expects a Country; consider an additive
  `nationality_sameAs` (GeoNames/Wikidata country) rather than only a free string.
- ○ **`relatedTo` / `knows` / `colleague`** — ❌ NOT ADOPTED (owner, 2026-10-27). Generic person-person
  edges (commander↔subordinate). A generic array is O(n²) + unsourced; we model command via groups/charts
  (M6). Revisit only on a specific need, as a narrow source-attributed command-relation array.

**Caveat:** schema.org `Person` has no rank, unit, service-number, or award-class vocabulary — our
military fields stay authoritative. Take `sameAs`/`identifier`/name-parts; do **not** flatten our
structured `military_awards`/`units_served`/`ranks` into schema.org's thinner equivalents.

---

## 2. `places_output.py`  →  schema.org [`Place`](https://schema.org/Place) (+ `GeoCoordinates`, `GeoShape`)

Strong overlap — we already have coordinates, bounding_box, hierarchy, related_places, aliases.

> **STATUS: ✅ IMPLEMENTED (places 2.29 → 2.30, 2026-10-27)** — the first schema.org adoption. Added as
> additive, nullable, CLOSED sub-schemas: `sameAs[]`, `containedInPlace`/`containsPlace` (ID-typed),
> `geo` (our GeoShape), `additionalProperty[]` ({propertyID,value,unitCode}), and a dedicated
> `elevation_m`. Backward-compatible (test_entity_schema_consistency); fingerprint re-pinned; shape
> archived to `src/schemas/history/places/2.30.json`; Change Log row added. (Population of these fields by
> the geocode/enrichment pipeline is a separate follow-up — this PR adds the schema only.)

- ⭐ **`sameAs`** (array of URL) — ✅ IMPLEMENTED. **GeoNames ID, Wikidata QID, OpenStreetMap relation/way**.
  The biggest win for places: disambiguates "Hill 400" / "St. Lô" against authoritative gazetteers and is
  the fix hook for the open **MED place-dedup-by-coords** issue (match on GeoNames/Wikidata ID, not string).
- ⭐ **`containedInPlace` / `containsPlace`** — ✅ IMPLEMENTED as ID-typed edge arrays
  (`{PlaceID, name}`). Distinct from the loose `hierarchy`/`related_places`: a traversable containment
  graph ("Vierville ⊂ Normandy ⊂ France").
- ◆ **`geo` → `GeoShape`** — ✅ IMPLEMENTED as a closed sub-schema `{shape_type: point|line|polygon|circle,
  vertices:[{lat,long}], radius_m, source_date, SourceSectionID, confidence}`. Adds line/polygon/circle
  beyond the point `coordinates` + box `bounding_box` — a beach (line), a sector/front (polygon), a river
  (polyline) — exactly the terrain M5 links + the sectors maps depict. Vertices as our own `{lat,long}`
  objects (NOT schema.org's `"lat,long"` string); populated only when a source/map gives the shape.
- ◆ **`additionalProperty`** — ✅ IMPLEMENTED as a closed `{propertyID, value, unitCode}` array. Typed
  escape hatch for long-tail military-geographic attributes with no dedicated field (`grid_reference`,
  `hill_number`, map designation). Named fields are preferred (see `elevation_m`); this is for the rest.
- ◆ **`elevation_m`** — ✅ IMPLEMENTED as a DEDICATED numeric field (metres), NOT inside
  `additionalProperty`. Decision (owner, 2026-10-27): elevation is a first-class, unit-fixed, queryable
  terrain attribute (Hill 400, the Hürtgen heights) and schema.org itself names `elevation` on
  GeoCoordinates — so it gets a named field; `additionalProperty` is only for the non-enumerable rest.
- ○ **`alternateName`** — equivalent to our `aliases`/`historical_names` (already covered; note the
  naming). Not separately added.

**Caveat:** schema.org `Place` is contemporary-gazetteer oriented. Our **historical** names and the
*as-of-date* of a place name (a town renamed/destroyed) have no schema.org slot — keep
`historical_names` + the event-mention date context. GeoNames `sameAs` points at the modern place;
record that it is a *modern-identity* cross-walk, not a claim the WWII place is identical.

---

## 3. `events_output.py` + `source_section_output.py`  →  schema.org [`Event`](https://schema.org/Event)

Our Event is a source-anchored container of Sub-events with people/places/dates/groups/equipment
arrays. schema.org `Event` is the closest structural match and suggests:

- ⭐ **`subEvent` / `superEvent`** — we have `Sub-events` nested; schema.org's explicit
  sub/super pair also models the **operation → battle → engagement** ladder that `source_section.operation`
  is reaching for. Consider a `superEvent` link from an Event to its campaign/operation entity.
- ⭐ **`about`** (array) — "subject matter" as typed links. Our Sub-events already carry
  people/places/groups arrays; `about` is the schema.org name for that and could unify the
  cross-entity link set.
- ◆ **`startDate` / `endDate`** (Date/DateTime) — our Event has no own date; dates live only in the
  `dates` entity + mentions. An additive derived `startDate/endDate` on the Event (from its
  sub-events' resolved date interval) would make events directly time-queryable. Pairs with the
  `dates` resolution work.
- ◆ **`location`** (Place) — likewise, a derived primary `location` link (dominant PlaceID) on the
  Event. Today location is only via sub-event place arrays.
- ◆ **`organizer` / `performer`** (on `source_section`/Event) — the commanding
  org/person. Overlaps M6 command charts (who ran the operation).
- ○ **`EventStatus` / `eventAttendanceMode`** — SEO/ticketing concepts; **not applicable**.

**Caveat:** schema.org `Event` is built for *scheduled, future, attendable* events (concerts,
webinars) — hence `offers`, `remainingAttendeeCapacity`, `doorTime`. **None of that applies.** Take
only `subEvent/superEvent`, `about`, `startDate/endDate`, `location`. Our Sub-event granularity +
ULID provenance is richer than schema.org here; do not collapse to its flat model.

---

## 4. `groups_output.py`  →  schema.org [`Organization`](https://schema.org/Organization)

Military units/organizations. Good overlap; our `military_hierarchy`, `parent_organization`,
`sub_organizations`, `unit_patch` are domain-specific strengths.

- ⭐ **`sameAs`** — Wikidata/Wikipedia for the unit (e.g. "29th Infantry Division" → Q-id). Unifies
  our `wikipedia_url`/`grokipedia_url` into a first-class cross-walk.
- ⭐ **`parentOrganization` / `subOrganization`** — ⚠️ REVISED DECISION (owner, 2026-10-27): do **NOT**
  adopt as flat fields. (schema.org cardinality: `subOrganization` is repeatable, `parentOrganization` is
  single — but BOTH lack a time dimension, which is disqualifying for order-of-battle.) Military
  attachment is **MOMENT-IN-TIME**, same principle as a person's titles (§1): a regiment may be organically
  ASSIGNED to its division permanently, but an armored combat command (CCA/CCB) is ATTACHED to a different
  division for a period, and a division moves between corps as the situation changes. Evidence:
  `contentrepository/European Theater of Operations - Order of Battle` is saturated with dated structure
  (activation dates, T/O&E revision dates, per-division arrival/combat dates). INSTEAD of flat parent/sub,
  add a time-bounded, provenance-carrying **`org_attachments[]`** edge array:
  `{role: parent|subordinate, attachment_type: assigned|attached|under_command, GroupID, name, from, to,
  source_date, SourceSectionID, confidence}` — ID-typed (traversable graph), the military
  **assigned (organic) vs attached (temporary)** distinction first-class (schema.org has no such
  vocabulary), each slice dated + provenance-tagged, NEVER overwriting an earlier attachment with a later
  one. Directly populated by the M6 chart-as-source command edges. (Implementation: a follow-on
  people_groups version bump, not the places PR.)
- ◆ **`memberOf` / `member`** — we have `members`; schema.org adds the inverse and a typed Person/Org
  member. Our people↔group link lives on the Person side (`group_affiliations`); `member` would mirror
  it on the group side. (NOTE: member tenure is ALSO moment-in-time — if added, it needs from/to like
  `org_attachments`, not a flat list.)
- ◆ **`foundingDate` / `dissolutionDate`** — a unit's activation/inactivation dates. No current slot.
  (These two ARE point-events, so single dated values are fine — unlike attachment, which is an interval.)
- ◆ **`logo`** → maps to our `unit_patch` (already richer: vision-verified, hashed, stored).
- ○ **`identifier`** — unit designation codes (e.g. the OOB `designation`/`echelon`/`unit_number` we
  already store on the Person's `units_served`) could be mirrored here as typed identifiers.

**Caveat:** schema.org `Organization` is company/brand oriented (`taxID`, `duns`, `naics`,
`contactPoint`) — ignore all of that. Military echelon/command-structure is ours to model; schema.org
only offers the generic parent/sub/member skeleton AND has no time dimension — so OOB attachment must be
our own time-bounded `org_attachments` edge, not schema.org's flat parent/sub.

---

## 5. `images_output.py` (+ people/places/groups inline `images[]`)  →  schema.org [`ImageObject`](https://schema.org/ImageObject) / [`MediaObject`](https://schema.org/MediaObject)

This is where schema.org is most directly useful — it has a mature media model and we are actively
ingesting images (M2/M3/M5).

- ⭐ **`contentUrl` / `thumbnailUrl`** — we have `url`/`local_copy`; schema.org separates the
  canonical content URL from a thumbnail. Minor but standard.
- ⭐ **`creditText` / `copyrightNotice` / `license` / `acquireLicensePage`** — Google Images'
  licensing metadata. We have `license`; schema.org's fuller set (`creditText`, `copyrightNotice`,
  `copyrightHolder`) matches our provenance goals and the bibliography `copyright_status` work.
- ⭐ **`contentLocation`** (Place) + **`about`/`depicts`** — "what/where the image shows." This is
  EXACTLY the M3 (image→Person) and M5 (image→Place) linkage. schema.org's `contentLocation` (the
  depicted place) and the `about` subject links are the vocabulary for what M3/M5 already compute.
  Our `PlaceMentionID`/`SourceSectionID`/`EventID` links are the ULID-strong version.
- ◆ **`isBasedOn` / `isPartOf`** — "this image came from source X." Our `SourceSectionID` IS this.
  Note the schema.org name for the attribution we just backfilled.
- ◆ **`dateCreated` / `datePublished`** — we have `extracted_date`/`url_capture_date` (ingestion
  dates); schema.org distinguishes *when the photo was taken* (`dateCreated`) — a historically
  meaningful field we don't capture.
- ◆ **`caption` / `exifData` / `representativeOfPage`** — `caption` overlaps our vision caption;
  `width`/`height`/`encodingFormat` are standard and we partly have them (`unit_patch` has w/h).

**Caveat:** take the licensing + content-location + caption vocabulary. Skip SEO/display fields
(`representativeOfPage`, `thumbnail` sizing hints). Our `needs_review` + vision `caption_confidence`
have no schema.org equivalent and stay.

---

## 6. `bibliography_output.py`  →  schema.org [`CreativeWork`](https://schema.org/CreativeWork) / [`Book`](https://schema.org/Book) / [`Article`](https://schema.org/Article)

Our bibliography is already citation-rich (author, publisher, ISBN, pages, volume, edition,
copyright_status, source_qualification). schema.org adds:

- ⭐ **`sameAs` / `identifier`** — WorldCat/OCLC number, LCCN, DOI, OpenLibrary ID, Wikidata. We have
  `isbn`; these broaden the cross-walk and improve the Archive.org/Gutenberg resolution the
  bibliography resolver already does.
- ◆ **`isBasedOn` / `citation`** — schema.org `citation` is "a work cited by this work." Our
  `mentions[]` is the inverse (where *we* cite this biblio). The schema.org `citation` direction could
  model a source citing another in-corpus source — overlaps the M8 intra-corpus graph.
- ◆ **`author` → `Person`** — our `citation.author` is a string array; schema.org expects `Person`.
  An additive `author_PersonID[]` would link a book's author to our Person entity when they are also
  a historical actor (e.g. a general who wrote a memoir — the bibliography already lists exactly these).
- ◆ **`abstract` / `inLanguage`** — a short work summary + language code. We have neither at the
  work level.
- ○ **`publisher` → `Organization`**, **`locationCreated` → `Place`** — typed instead of string.

**Caveat:** schema.org has no equivalent of our **`source_qualification`** (primary/secondary,
proximity, evidentiary support) or **`copyright_status`** determination — these are research-grade
and stay ours. `availability`/`download_status`/`search_status` are pipeline-internal, not
schema.org concepts.

---

## 7. `dates_output.py`  →  schema.org `Date` / `DateTime` (+ no good interval type)

- ◆ **`startDate` / `endDate`** naming — we use `date_start`/`date_end`; schema.org's camelCase is the
  convention if we ever emit JSON-LD.
- ○ Little else to take.

**Caveat (important):** schema.org dates are **ISO-8601 points**; it has **no first-class fuzzy
interval / precision** type. Our `resolved_earliest`/`resolved_latest`/`date_precision`/
`resolution_method` are STRICTLY richer and are a core research requirement. **Do not** regress to a
single schema.org `Date`. If emitting JSON-LD, map our interval to a `startDate`/`endDate` pair and
keep our fields as the authority.

---

## 8. Smaller / domain-specific schemas (brief)

- **`casualties_output.py`, `logistics_output.py`, `weather_output.py`** — **no meaningful schema.org
  type.** schema.org has no casualty, order-of-battle-logistics, or historical-weather vocabulary.
  `weather` is closest to no-type (schema.org weather is forecast/SEO, not historical observation).
  **Recommendation: leave as-is**; only consider a generic `sameAs` if a record maps to a Wikipedia
  article (rare).
- **`maps_output.py` / `map_features_output.py`** → schema.org [`Map`](https://schema.org/Map) is a
  thin `CreativeWork` subtype (only `mapType`). Our map-as-source + `map_features` (vision-extracted
  units/places/movements) are far richer. Take only `sameAs` (if a map has a Wikimedia/archive URL)
  + the `ImageObject` licensing fields (§5). The `spatialCoverage` property (Place/GeoShape of what
  the map covers) is a good additive for "which sector this map depicts."
- **`source_section_output.py`** → besides the `Event` mapping (§3), schema.org `CreativeWork`
  `isPartOf`/`hasPart` models chapter⊂book cleanly, and `citation` models the `reference_articles`
  we capture.

---

## 9. Cross-cutting recommendations (priority-ordered)

0. ⭐⭐ **GOVERNING PRINCIPLE — stateful attributes are MOMENT-IN-TIME, never flat.** schema.org models
   an entity's *current* state (one `honorificPrefix`, one `parentOrganization`). Our corpus is
   historical: a person's **title/rank/office** (General Marshall 1944 → Secretary of State 1947) and a
   unit's **attachment** (CCA attached to a different division for a period; a division moving between
   corps) are only true within a date interval, anchored to the SOURCE's date. ANY such attribute we add
   must be a **time-bounded dated array with provenance** (`from`/`to`/`source_date`/`SourceSectionID`),
   not a flat value or an unordered list. This already governs: Person `titles`/`honorifics` (§1) and
   people_groups `org_attachments` (§4).
1. ⭐ **Introduce a first-class `sameAs: [url]` (nullable array) on the top-level entities**
   (`people`, `places`✅, `people_groups`, `bibliography`, and `maps`). Single, uniform,
   additive, high interoperability value; the natural home for the Wikidata/Wikipedia/GeoNames/VIAF
   IDs we already partially fetch. **Places done (2.30);** roll out to the others next.
2. ⭐ **Add a typed `identifier: [{propertyID, value}]` (PropertyValue) pattern** where we have
   external codes (service numbers, OCLC/LCCN, GeoNames). Reuse one shared sub-schema.
3. ◆ **Evolve string hierarchy links toward ID-typed links** — places `containedInPlace`/`containsPlace`
   DONE (2.30, ID-typed). people_groups is NEXT but as the **time-bounded `org_attachments`** edge (NOT
   flat parentOrganization/subOrganization — see §4), which also carries the M6 command edges.
4. ◆ **Adopt the `ImageObject` licensing + `contentLocation`/`dateCreated` vocabulary** for image
   records (§5) — closest, lowest-risk win, and our image pipeline is active.
5. ○ **Only if we ever publish JSON-LD / sitemaps for search engines**: add a serialization layer that
   maps our entities to schema.org `@type`/`@id`/`sameAs` at EXPORT time — keeping the internal
   schema strict and the schema.org shape as a *view*, not the storage model. (This is the cleanest
   way to get the SEO/discovery benefit without weakening the research corpus.)
6. ◆ **FOLLOW-ON (people_groups):** implement the time-bounded `org_attachments[]` edge array (§4) —
   `{role, attachment_type: assigned|attached|under_command, GroupID, name, from, to, source_date,
   SourceSectionID, confidence}` — the OOB order-of-battle attachment graph. Separate version bump;
   populated by M6 chart-as-source.

**Guiding principle:** borrow schema.org's **linking vocabulary** (`sameAs`, `identifier`,
`containedInPlace`, `subEvent`, `about`, `contentLocation`, `isBasedOn`) and its **media/licensing**
model; keep our **identity (ULID), provenance (SourceSectionID), source-qualification, fuzzy-date, and
moment-in-time stateful-attribute** models, which are stricter and more research-appropriate than
anything schema.org offers.

---

## 10. What NOT to take (explicit non-goals)

- SEO/display/ticketing fields: `offers`, `aggregateRating`, `review`, `potentialAction`,
  `breadcrumb`, `mainEntityOfPage`, `representativeOfPage`, event `doorTime`/attendance.
- schema.org's **open/loose typing** (`additionalProperty` as a general escape hatch) — it conflicts
  with our `additionalProperties:false` + write-guard discipline. Use named, nullable fields instead.
- Replacing ULID identity with URL `@id` identity — we keep ULID; `sameAs` is the bridge, not a
  replacement.
- Collapsing our fuzzy-date / source-qualification / military-domain fields into thinner schema.org
  equivalents.
