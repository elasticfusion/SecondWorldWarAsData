# Grok Prompt and Model Recommendations

**Date:** 2026-06-14  
**Author:** Grok (based on deep dive of `/prompts/` (all 11 files), `config.yaml` + all `*.example` profiles, `src/utils/prompt_loader.py`, `src/grok_client.py`, extractors, `tests/test_prompt_schema_alignment.py`, `scripts/deploy_all.sh`, `docs/current/core/PROMPT_MANAGEMENT.md`, `CONFIGURATION.md`, `PIPELINE.md`, prior CODE_INTEGRITY_REVIEW, and the dataCritical change review)  
**Status:** Recommendations only — no code changes made. New backlog items were added to [docs/current/TODO.md](TODO.md) (see the "Prompts, Configuration, Model Routing & LLM Integration" subsection under Medium and the new Critical items).

---

## Executive Summary

The pipeline has made excellent progress externalizing prompts to YAML (via `render_prompt` + rules appending + S3 override support in the loader). This improves auditability, versioning potential, and prod overrides.

However, the transition is incomplete:
- User prompt templates are largely centralized.
- System prompts are still fragmented (legacy constants in `.py` files often override or duplicate YAML versions).
- Model routing (`api.grok.model_map`) is under-utilized — only trivial lookup/verification tasks route to the cheaper/faster `grok-3-mini`.
- Supporting infrastructure (templating safety, prompt versioning, test coverage, cache key integration) lags behind.

**Grok-specific opportunities (as of grok-4.3 / 2026):**  
Grok models excel at long-context reasoning, strict instruction following ("CRITICAL", "ONLY extract explicitly mentioned", "COPY the ID exactly from the list above"), and producing clean structured output when given clear schemas + calibration examples. `grok-3-mini` is particularly strong and cost-effective for classification, ID resolution, grounding tasks, and simple extraction — while the flagship `grok-4.3` (or successor) shines on narrative synthesis, biographical nuance, event grouping, citation parsing, and hierarchy understanding.

**Key recommendations (high impact / low risk first):**
- Finish system prompt centralization and enforce `get_system_prompt(name)`.
- Add a `version:` field to every prompt YAML; wire it into cache keys and validation.
- Dramatically expand `model_map` in cost-optimized profiles (map dates, places, weather, casualties, logistics, etc. to `grok-3-mini`).
- Add few-shot examples + explicit internal-reasoning steps in complex prompts (leverages Grok reasoning).
- Make "Available entities" / ID-copy blocks structured (JSON) for higher fidelity.
- Expand prompt schema alignment tests to all 11 types + new fields.
- Move the remaining inline prompts into `/prompts/`.

These changes will improve maintainability, reduce token spend (via better routing + fewer retries), increase determinism/grounding, and make prompt iteration safer.

Many of these map directly to new TODO entries added during the 2026-06-14 reconciliation (sourced from this deep dive + change review).

---

## Current State (as of 2026-06-14)

### Prompts (`/prompts/*.yaml`)
- 11 files: `events.yaml`, `people.yaml`, `places.yaml`, `dates.yaml`, `people_groups.yaml`, `biography.yaml`, `equipment.yaml`, `weather.yaml`, `logistics.yaml`, `casualties.yaml`, `supplemental.yaml`.
- Structure: `system_prompt`, `prompt_template` (with `{placeholders}`), `schema` (illustrative JSON example), `rules` (list of bullets, appended at render time).
- Loader (`src/utils/prompt_loader.py`):
  - `load_prompt(name)` — local first, S3 override if AWS enabled.
  - `render_prompt(name, **kwargs)` — injects `{schema}` if referenced, appends rules, does naive `{key}` string replace.
  - `get_system_prompt(name)`.
- Strengths: Excellent anti-hallucination rules (especially in people, logistics severity calibration, casualties "COPY ID", supplemental semicolon splitting, equipment category/subcategory examples, weather "only exact dates").
- Gaps: Partial centralization (see below), no `version`, naive templating, limited few-shots, some very long prompts, "Available entities" blocks are free-text (fragile).

### Model Routing & Config
- Default: `grok-4.3` (flagship).
- `model_map`: Only 5 trivial keys (`isbn`, `url_verify`, `copyright`, `nara_match`, `openserp_verify`) map to `grok-3-mini` in cost-optimized/balanced profiles. Everything else (including dates, places, weather, casualties, logistics, equipment extraction, people, events, supplemental, etc.) falls through to the default.
- `grok_client.py`: `_get_model(cache_type)` is a simple lookup. Cache keys already include the resolved model (good — switching models invalidates relevant cache). Temperature is mostly hardcoded in callers (0.1 extraction, 0.0 verification).
- Profiles (`config.*.yaml.example` + active `config.yaml`):
  - `cost-optimized`: Conservative rate limits, batch phase2, limited external searches, equipment vision/enrichment off.
  - `performance-optimized`: Empty `model_map` (use best everywhere), higher concurrency/rate limits, shorter timeouts.
  - `review-all-data` / `balanced`: Varying degrees of thoroughness.
- `calls_per_minute`, `max_retries`, `timeout` are profile-tunable.
- Batch API (50% savings) is respected for routing.

### Supporting Code & Tests
- Many extractors still define/pass `SYSTEM_PROMPT = """..."""` (events, dates, places, people, weather_central, supplemental, etc.). YAML `system_prompt` fields exist in most cases but are not consistently used.
- Inline prompts remain outside `/prompts/` (supplemental_search, people_consolidation, equipment sub-calls, openserp_maps license, various enrichment/map prompts).
- `test_prompt_schema_alignment.py`: Covers only 8 types; limited depth.
- `scripts/deploy_all.sh`: Validates presence of `prompt_template` + parsable `schema`.
- `grok_client.py` has good JSON repair, empty-content guards (recent), prompt validation, and cache stats.

---

## Cross-Cutting Prompt Recommendations

1. **Complete system prompt centralization**  
   Delete all remaining `SYSTEM_PROMPT` constants in `.py` files. Replace `system_prompt=SYSTEM_PROMPT` (or inline strings) with `system_prompt=get_system_prompt("events")` (or the appropriate name). Update `PROMPT_MANAGEMENT.md` and any call sites in `batch_parallel.py`, individual extractors, and enrichment. This eliminates drift and makes the YAMLs the single source of truth.

2. **Add explicit `version:` field to every YAML**  
   Example:
   ```yaml
   version: "2.3"
   system_prompt: ...
   prompt_template: ...
   schema: ...
   rules: [...]
   ```
   - Bump on every meaningful change.
   - Wire the version into cache keys in `grok_client.py` (e.g., include in `_make_cache_key`).
   - Update `deploy_all.sh` validator and `test_prompt_schema_alignment.py` to require and test the version field.
   - Document a policy: "Changing a prompt version invalidates relevant caches for that entity type."

3. **Harden / replace the templating in `prompt_loader.py`**  
   The current `for key, value in kwargs.items(): prompt = prompt.replace(...)` is simple but risky with JSON examples containing braces.  
   Recommendations:
   - Use a safer mechanism (e.g., `string.Template` with a restricted set of known variables, or a minimal safe formatter).
   - Keep the existing `_validate_prompt` guard but make the placeholder regex per-prompt (whitelist only the variables declared for that prompt).
   - Consider injecting a small "template_vars" section in the YAML for validation.

4. **Expand prompt schema alignment testing**  
   - Cover all 11 types (add `supplemental`, `people_groups`, `biography`).
   - Test `system_prompt` presence when present.
   - Test that `rules` are appended correctly.
   - Test that the *rendered* prompt (with schema + rules) + example produces output that validates against the actual runtime Pydantic models / JSON schemas used by the extractor (not just the illustrative schema in the YAML).
   - Add a test that changing `version` affects cache key behavior (or at least that the version is loadable).

5. **Add few-shot examples + explicit internal reasoning**  
   Grok responds very well to:
   - "First reason step-by-step internally about X using the calibration rules below. Then output *only* the final JSON — no reasoning text in the response."
   - 2–3 concrete before/after JSON examples for the hardest decisions (logistics severity, casualty type/side, supplemental classification, equipment category vs subcategory, people plural-rank handling).
   - Store the examples in the YAML under an `examples:` or `few_shots:` key and render them after `rules`.

6. **Make "Available entities" / ID-copy blocks machine-readable**  
   In `casualties.yaml`, `weather.yaml`, etc., the current free-text "Available entities (COPY these IDs...)" is powerful but formatting-sensitive.  
   Recommendation: Render a compact JSON block (or labeled sections with `PeopleGroupID: 01ULID... | name: ...`) and strengthen the instruction:
   > Perform an *exact* ID match first. Only fall back to normalized name/partial match if no exact ID hit. Never invent a new ULID for reference fields.

7. **Move remaining inline prompts into `/prompts/`**  
   Target files (non-exhaustive):
   - `supplemental_search.py`
   - `people_consolidation.py` (the consolidation prompt)
   - `equipment.py` (enrichment, media, image verification sub-prompts)
   - `openserp_maps.py` (license terms)
   - Various Phase 3 biography enrichment prompts and map search prompts.
   This gives them S3 override, deploy validation, and central review.

8. **Grok-specific prompt engineering techniques to adopt**
   - "You are a precise WWII historical data extraction specialist. Ground *exclusively* in the provided source text. Do not use external knowledge."
   - Explicit "Do NOT fabricate", "If not explicitly stated, use null", "Return empty array/object if none found".
   - For verification / zero-creativity tasks: temperature 0.0 + "Answer only with the exact value or null".
   - Leverage long context: prefer sending full relevant sub-event + entity context rather than aggressive truncation (Grok handles 128k+ well).
   - Anti-hallucination: repeat critical rules at the end of the prompt ("CRITICAL: re-read the rules above before outputting.").

---

## Per-Prompt Recommendations (Prioritized)

**High impact / frequently executed:**
- **events.yaml**: Strengthen sub-event grouping guidance ("a coherent tactical or operational action with a distinct outcome or location shift"). Keep the paragraph-numbers-only optimization (excellent token savings). Ensure the YAML `system_prompt` is the one used after migration.
- **people.yaml**: Already one of the strongest. Add 1–2 few-shot examples for the "Admirals Leahy and King" plural-rank case and title/position references. Make `biography_sources` fields always populated when any biographical data is taken from the source.
- **logistics.yaml + casualties.yaml**: Turn the excellent prose severity / count qualifier calibration examples into 2–3 actual few-shot "source text → correct JSON" pairs. Add the internal-reasoning step instruction.
- **weather.yaml + casualties.yaml**: Convert the "Available places/dates/entities" blocks to structured JSON for higher ID-copy accuracy.
- **supplemental.yaml**: Add explicit decision criteria / examples for `classification: document_reference | factual_content | ambiguous`. Keep and expand the semicolon-splitting rule.

**Others:**
- **places.yaml**: Add guidance on linear features (rivers, fronts) and very large areas (oceans, "the Western Front").
- **dates.yaml**: Add more prefix-format examples (mid-1944-07, summer-1942, early-1944) and a rule against fabricating days.
- **people_groups.yaml**: Add abbreviation normalization examples ("2nd ID", "1st SS Panzer" → official name + `common_name`).
- **equipment.yaml**: Expand the negative examples ("do not extract generic 'tanks' unless a specific model is named"). Add rules for when to populate `variants` / `specifications`.
- **biography.yaml** (Phase 3): Strengthen "source_urls and references must come only from the provided Wikipedia/Grokipedia text."
- **prompts that still have legacy SYSTEM_PROMPTs**: After centralization, review the YAML version against the old `.py` version and keep the tighter one.

---

## Model & Configuration Recommendations

### 1. Expand `model_map` for cost/quality optimization (highest quick win)

In `config.cost-optimized.yaml.example` (and `balanced`):

```yaml
api:
  grok:
    model: "grok-4.3"
    model_map:
      # Trivial / verification (already present)
      isbn: "grok-3-mini"
      url_verify: "grok-3-mini"
      copyright: "grok-3-mini"
      nara_match: "grok-3-mini"
      openserp_verify: "grok-3-mini"

      # Recommended additions — lighter grounding / classification / ID-copy tasks
      dates: "grok-3-mini"
      places: "grok-3-mini"
      weather: "grok-3-mini"
      casualties: "grok-3-mini"
      logistics: "grok-3-mini"
      # people_groups partial extraction can also be cheap in many cases
      peoplegroups: "grok-3-mini"   # note spelling matches runtime cache_type

      # Keep flagship for complex narrative, biographical nuance, citation parsing, hierarchy, equipment ID
      # (events, people, supplemental, equipment, biography, maps that need deep understanding)
```

- In `performance-optimized`: keep empty map or explicitly map everything to the current flagship.
- In `review-all-data`: empty map (or force flagship) is appropriate.
- Document every possible `cache_type` key (from `grok_client.CACHE_TYPES` + callers) with recommended model + rationale in `CONFIGURATION.md`.

### 2. Make temperature configurable per task
Add under `api.grok`:
```yaml
temperatures:
  default: 0.1
  verification: 0.0
  classification: 0.0
  extraction: 0.1
```
Wire it in `grok_client` (fall back to 0.1) and pass through `extract_*` / `chat_completion` calls. Update callers that currently hard-code temperature.

### 3. Other config / client improvements
- Drive preview sizes (`debug_message_preview_chars`, `debug_response_preview_chars`) from profile + logging level (larger on DEBUG or in review-all-data).
- Consider a small "task_complexity" hint or per-prompt override in the YAML that influences model choice (future).
- In batch paths, ensure the chosen model from `model_map` is correctly serialized into the batch JSONL.
- Update `CONFIGURATION.md` with a table of recommended model_map entries + expected cost/quality trade-offs.
- Add a lightweight "prompt + model matrix" test (or script) that can be run against a small chapter to validate routing + quality after changes.

### 4. Grok family notes (2026)
- `grok-3-mini`: Fast, cheap, excellent at following explicit copy/enum/grounding rules and producing small structured output. Ideal for the tasks listed above.
- `grok-4.3` (flagship): Superior at long-context synthesis, disambiguating people/groups from context, understanding military hierarchy and operational intent, and handling the more open-ended summarization + biographical work.
- Both support the Batch API (50% savings) and vision where used.
- Temperature 0.0 is very effective on Grok for deterministic tasks; 0.1 is a good default for extraction without excessive creativity.

---

## Prioritization & Migration Notes

**High priority (do these first):**
- System prompt centralization + uniform use of `get_system_prompt`.
- Add `version:` + wire into cache keys + update tests/deploy validator.
- Expand `model_map` in cost-optimized profile + document it.
- Expand alignment tests to all 11 + new fields.

**Medium:**
- Structured "Available entities" blocks + few-shots + internal reasoning instructions.
- Move remaining inline prompts.
- Temperature configuration.
- Hardening of prompt templating.

**Lower / future:**
- Prompt-aware complexity / model routing policy.
- Full Jinja2 (restricted) templating if replace ever becomes a real problem.

**Testing after changes:**
- Clear relevant `cache/api/...` directories.
- Run with `--max-items 5` or a single book.
- Run the (expanded) prompt alignment tests.
- Compare output quality + token spend between profiles.

See the concrete backlog items added to `TODO.md` (under the new Prompts subsection and the three new Critical items from the change review) for tracking.

---

## References & Related Docs
- `docs/current/core/PROMPT_MANAGEMENT.md`
- `docs/current/core/CONFIGURATION.md`
- `docs/current/TODO.md` (new entries added 2026-06-14)
- `src/utils/prompt_loader.py`
- `src/grok_client.py`
- `tests/test_prompt_schema_alignment.py`
- `scripts/deploy_all.sh`
- Individual files under `prompts/`

This document is the canonical source for the prompt + model recommendations coming out of the 2026-06-14 deep dive. Update it (and bump prompt versions) as work progresses.

*Turning history into high-quality, cost-efficient structured data — one well-engineered prompt and model choice at a time.*

---

## Data Scientist Evaluation (2026-06-14)

**Reviewer:** Data Science Agent (Kiro)

### Overall Assessment: Strong, with caveats

This document is thorough and well-organized. The recommendations align with observed data quality issues from our end-to-end reviews. Key agreements and disagreements:

### Agree ✅

- **System prompt centralization** — We've seen drift between YAML and inline constants cause subtle extraction differences across runs. Single source of truth is correct priority.
- **`model_map` expansion to grok-3-mini** — Dates, places, weather, and logistics are well-constrained extraction tasks. Our data shows these produce clean output already; a cheaper model with strict instructions should maintain quality. The cost savings would be substantial (these 4 types represent ~18K entities = majority of API calls).
- **Structured "Available entities" blocks** — Our earlier analysis showed 81% broken PlaceIDs in weather and 86% null PeopleGroupIDs in casualties. The current free-text format is demonstrably failing. JSON-formatted entity lists with explicit copy instructions (which we partially implemented) are the right direction.
- **Few-shot examples for logistics severity** — We confirmed the calibration text alone didn't move the needle (still 78% high/critical). Few-shots are the logical next step.
- **Prompt versioning** — Cache invalidation after prompt changes has already bitten us (stale "Ibid" titles from cached responses). Versioned prompts would prevent this class of bug.

### Disagree / Nuance ⚠️

- **"Move remaining inline prompts into /prompts/"** — Partially disagree. Enrichment prompts (biography, openserp verification) are tightly coupled to their calling code's response parsing logic. Externalizing them adds indirection without clear benefit since they're only called from one place. The extraction prompts (Phase 2) should be external; the enrichment prompts (Phase 3) can stay inline. Prioritize based on how often they change.
- **"Leverage long context: prefer sending full relevant sub-event"** — This contradicts the cost optimization goal. Our date extraction cost optimization (summary vs fulltext) saves 10-15% with acceptable quality. The recommendation should be: use full context for complex tasks (events, people, supplemental), use summaries for simple tasks (dates, places) where the risk of missing data is low.
- **Temperature configurability per task** — Over-engineering for current needs. The existing pattern (0.1 extraction, 0.0 verification) covers all current use cases. Adding config complexity for a setting that's changed approximately never isn't worth the maintenance burden.

### Missing from this document

- **No mention of the `Supplemental_Materials` vs `Supplemental_Material` key mismatch** — This is a live bug causing data loss right now. Should be listed as Critical.
- **No mention of the `additionalProperties: false` validation issue** — This dropped all Ardennes casualties/equipment. Prompt schema alignment tests should catch this pattern (prompt produces fields X, validator rejects fields X).
- **No cost estimates** — The model_map expansion claims savings but doesn't quantify. With ~18K entities across dates/places/weather/logistics, switching to grok-3-mini at (roughly) 1/3 the cost would save ~$X per full run. Worth calculating.
- **No discussion of retry/fallback model strategy** — When grok-3-mini produces invalid output (fails validation), should it retry with grok-3-mini or escalate to the flagship? This matters for the quality/cost tradeoff.

### Priority Adjustment

The document's priorities are:
1. System prompt centralization
2. Prompt versioning
3. Model map expansion
4. Test expansion

**Recommended reordering based on actual impact on data quality:**
1. Fix the `additionalProperties` schema/prompt mismatches (data loss right now)
2. Model map expansion (immediate cost savings, low risk)
3. Few-shot examples for logistics + structured entity blocks for casualties/weather (fixes known quality gaps)
4. System prompt centralization + versioning (maintainability, not data quality)

The document is valuable as a roadmap. Implementation should be sequenced by data quality impact, not architectural purity.
