# Deterministic Event IDs — Design (HIGH-1 #1)

**Status:** proposed (2026-10-10). Root-cause fix for the cross-reference staleness/ripple class.
Pairs with the enforcement pass shipped in PR #324 (that DETECTS dangling refs; this PREVENTS them).

## Problem

`EventID` / `Sub-eventID` are taken verbatim from the Grok response (`events.py` reads
`result["EventID"]`), i.e. a fresh, **non-deterministic** value every extraction run. Consequences:

- **Staleness/ripple:** re-extracting a chapter mints NEW event IDs; every dependent already written
  (maps/weather/casualties/images/source_section) still points at the OLD id → dangling. This is the
  confirmed cause of the corpus dangling edges (maps→events 100%, etc.).
- **No validation of the LLM-supplied ID:** a hallucinated/placeholder/duplicate ULID from the model
  is trusted as-is (historically produced `01H7M…`-style placeholders).

## Goal

Make an event's identity a **pure function of its stable content**, so:
1. Re-extraction of the same source unit yields the **same** EventID (idempotent → no ripple).
2. The ID no longer depends on the LLM (the model's value is validated/replaced, not trusted).
3. Existing dependents keep resolving across re-runs.

## Identity key (what makes an event "the same event")

Derived only from stable, extraction-time fields already present in `parsed_data`:

- **EventID** = `f(book, chapter_title, section_id)` — one Event per source section. `book` +
  `chapter_title` are the natural chapter identity; `section_id` (the real parsed-data key —
  `'a'/'b'/...`, or `'<base>-<chunk>'` for Phase-1 chunk-split files, default `'full'`)
  disambiguates intentionally-split sections. NOTE: reading the wrong key (`section`) collapses all
  sections of a chapter onto one id — read `section_id`.
- **Sub-eventID** = `f(EventID, sub_event_ordinal_or_name)` — stable WITHIN its parent event.
  Preferred key: the sub-event's **paragraph range** (`absolute_number` start–end) because it is
  objective and stable across runs; fall back to a normalized `Sub-event_Name` only if the range is
  absent. (Name-only is less stable — the LLM can reword — so range-first.)

Normalization: lowercase, collapse whitespace, strip punctuation on the text components before
hashing, so trivial reformatting does not change identity.

## Minting a ULID-shaped deterministic ID

The schema requires `^[0-9A-HJKMNP-TV-Z]{26}$` (Crockford base32, 128-bit ULID shape). A ULID is
48-bit timestamp + 80-bit randomness. For a deterministic ID we fill those 128 bits from a hash of
the identity key instead of time+random:

```
digest = sha256(("event|v1|" + book + "|" + chapter + "|" + section).encode()).digest()
det_id = ulid.from_bytes(digest[:16])   # 128 bits -> valid 26-char Crockford ULID
```

- A **version tag** (`v1`) is included in the hash input so we can intentionally re-key later without
  silent collision.
- Collision risk: 128-bit space from sha256 → negligible. Distinct sections → distinct ids.
- The result validates against the existing ULID regex (no schema change to the ID FORMAT).

## Where it plugs in (single, central helper)

New central helper `src/utils/deterministic_ids.py`:
- `event_id(book, chapter, section="full") -> str`
- `sub_event_id(event_id, *, para_start=None, para_end=None, name=None) -> str`

Applied in `events.py` AFTER the LLM response is parsed, BEFORE validation/write:
- Overwrite `Event.EventID` with `event_id(parsed_data["book"], parsed_data["chapter_title"],
  parsed_data.get("section","full"))`.
- Overwrite each `Sub-events[].Sub-eventID` with `sub_event_id(...)` from its paragraph range.
- This REPLACES the LLM-supplied value (validation of the LLM ID = "we don't trust it; we derive it").

Centralized so maps/images/source_section (which READ the event's EventID at extraction time) all see
the same deterministic value — no per-extractor change needed; they already read it from the event.

## Migration (existing corpus)

The ID FORMAT is unchanged (still a 26-char ULID), so no schema migration. But existing event
records carry OLD random IDs, and dependents point at them. Options:
1. **Forward-only (preferred for dev):** on the next full re-extraction, events get deterministic IDs
   and dependents are re-extracted/re-linked in the same run; the enforcement pass (PR #324) confirms
   dangling→0. Dev/test data is refreshed from prod anyway (per the #3 decision), so no one-off
   migration script.
2. **In-place re-key (only if a corpus must be preserved):** a one-time pass that recomputes each
   event's deterministic ID and rewrites every referrer via the existing
   `merge._replace_id_in_obj` cross-ref rewriter. Heavier; not needed for dev.

Prod re-extracts forward only (per project convention), so option 1 is the plan.

## Testing

- `event_id` is pure/stable: same inputs → same id across calls/processes; different section → different id.
- Output matches the ULID regex (Crockford base32, 26 chars).
- `sub_event_id` stable under paragraph range; deterministic given the same parent + range.
- events.py integration: a re-extraction of the same parsed_data produces an identical EventID
  (the ripple-prevention guarantee), and the id survives `validate_event_json`.
- The LLM-supplied EventID is IGNORED (a response with a bogus EventID still yields the derived id).

## Out of scope / follow-ups

- Tightening the loosely-typed `casualties.impacted_*` schemas (separate HIGH-1 precision gap).
- Deterministic IDs for OTHER entity types (people/places) — those are entity-resolution/dedup keyed,
  a different mechanism; this doc is events only.
