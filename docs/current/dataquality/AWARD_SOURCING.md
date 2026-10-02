# Award Citation Sourcing — Design & Findings

How the pipeline obtains **verbatim award citations** for people named in an award
context, from **authoritative sources** (not search engines), with a full source
record. US awards first (Distinguished Service Cross); extensible to other
countries. Status: foundation built (schema + pluggable interface + offline source
+ gating); first alternate direct source pending source URLs.

---

## Why not just search?
The prior path (`openserp_enrichment.search_valor`) issued search-engine queries
("{name} valor defense.gov") and filtered by domain — slow, imprecise, and not an
authoritative record. The goal is to go **directly** to authoritative
award-recipient records and store the citation text + provenance.

## Finding: valor.defense.gov is Akamai-WAF-blocked at the IP level
`valor.defense.gov` (DoD Hall of Valor) lists recipients but the index pages carry
no citation text, and the site is behind an **Akamai edge WAF** that denies this
environment's requests:
- Verified 2026-10-02: `curl` (default + Chrome UA) → **403 Access Denied**
  (`server: AkamaiGHost`, `errors.edgesuite.net`); even `/robots.txt` 403s.
- Headless Playwright Chromium (incl. `--disable-blink-features=AutomationControlled`,
  webdriver masking, full browser context, 6s challenge wait) → **still 403**, and
  the site **root** 403s too — i.e. the block is at the **IP/network layer**
  (datacenter/cloud IP reputation), not a browser-challenge the headless client
  fails to solve. A real residential browser (e.g. Brave) loads it fine.
- **Implication:** a headless browser *in the pipeline container* (AWS egress) does
  NOT solve this — AWS IPs are blocked at least as hard. Headless-in-container was
  therefore NOT built.

## Decision (owner, 2026-10-02)
1. **Target ALTERNATE authoritative sources** that serve our IP (Military Times
   Hall of Valor, HomeOfHeroes, American War Library, or a published dataset —
   exact list pending). Polite direct fetch: descriptive UA, rate-limited, cached.
2. **Offline/bulk-dataset source** — query a locally-held recipients+citations
   dataset (zero live scraping). Built now; works immediately when a dataset is
   provided.
3. **Residential egress** (proxy / Tailscale exit node) for valor.defense.gov —
   **last resort only**, documented not built, used only if a citation is uniquely
   available there.

## Architecture (built)
- `src/enrichment/award_sources.py` — pluggable `AwardCitationSource` protocol +
  `enrich_person_awards(person, sources)` orchestration. **Gated**: runs only for
  `nationality == USA` people with ≥1 award (the "award context"); other
  nationalities are a future extension with their own sources. Gap-fill only
  (never overwrites an existing citation). Verification-not-fabrication: a citation
  is attached only when `verified` and the award matches.
- `src/enrichment/award_offline_source.py` — `OfflineAwardDataset`: a local JSON
  dataset `[{name, award, citation, source_name, source_url}]`, conservative name
  matching (normalized exact → last-name+first-initial), marks results `verified`.
- Provenance on `MilitaryAward` (`src/extraction/people.py`): `citation_text`,
  `source_name`, `source_url`, `retrieved_date`, `verified` — all optional
  (migration-safe). *(Note: a shadowed, unused `src/schemas.py` also defines a
  MilitaryAward; the live one is in `src/extraction/people.py`.)*

## Pending
- Replace/retire the OpenSERP `search_valor` path now that Hall of Valor covers
  US awards (keep OpenSERP only as a last-ditch fallback if desired).

## Source map (verified 2026-10-02)
| Source | Access from our IP | Role |
|---|---|---|
| **Hall of Valor** (valor.militarytimes.com) | **200, nginx, permissive robots** | **PRIMARY** — largest citation DB; MoH + all service crosses, partial Silver Star. BUILT + wired. |
| Home of Heroes (homeofheroes.com) | 200 (Cloudflare) | Secondary; WWII browsing. Future source. |
| CMOHS (cmohs.org) | **403 (Cloudflare)** on pages | MoH citations; Cloudflare-blocked like valor.defense.gov — defer / needs residential egress. |
| valor.defense.gov | **403 (Akamai, IP-level)** | Name-list PDFs only (no citations). Not used. |
| NARA (Navy Awards Citations Files NAID 599836; Army award cards/GOs) | n/a (not digitized) | Silver Star + unlisted awards; offline/manual → feed `OfflineAwardDataset`. |

Coverage reality (per owner): MoH essentially complete; DSC/Navy Cross strong;
Silver Star only partial for citation TEXT (Army alone awarded ~70k+). Unlisted
Silver Stars route to NARA/Fold3 general orders → the offline dataset path.

## Built this iteration
- `src/enrichment/award_hall_of_valor.HallOfValorSource` — polite (>=1 req/s,
  on-disk cache, descriptive UA honoring the permissive robots), `?s=` search →
  `/recipient/recipient-<id>/` → parses per-award citation prose ("presenting the
  <AWARD> to ... for <gallantry> in action ..."); name+award matched →
  `verified=True`. Live-verified: Audie Murphy → 4 citations (Silver Star, DSC,
  Bronze Star, Medal of Honor) correctly attributed; award-hint filter works.
- Wired into `enrich_person_biography` via `_source_award_citations` — opt-in
  (`AWARD_CITATIONS_ENABLED=true`), gated US + award context, fail-safe, records
  full provenance. Preferred over the OpenSERP `search_valor` path.

