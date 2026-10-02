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

## Access reality — CORRECTED by rigorous testing (2026-10-02)

**The dominant factor is REQUEST HEADERS, not the IP.** Earlier probes used bare
`curl` (UA + Accept only) and were wrongly read as hard WAF/JS blocks. A **full
browser header profile** — `User-Agent` + `Accept-Language` + `sec-ch-ua` /
`sec-ch-ua-mobile` / `sec-ch-ua-platform` + `Sec-Fetch-Dest/Mode/Site/User` +
`Upgrade-Insecure-Requests` — changes the result dramatically.

**Tested from AWS Lambda (the pipeline's real datacenter egress) with full headers:**
| Source | AWS + full headers | Note |
|---|---|---|
| London Gazette | **200 ✅** | earlier "challenge" was missing headers, not the IP |
| CMOHS | **200 ✅** | earlier 403 was missing headers |
| TracesOfWar | **200 ✅** | earlier 403 was missing headers |
| Victoria Cross Online | **200 ✅** | |
| Hall of Valor | **200 ✅** | |
| **valor.defense.gov** (Akamai) | **403** | the ONLY holdout — Akamai still blocks the AWS IP even with full headers; serves 200 from a residential IP. Carries name-lists only (no citations) → negligible loss. |

**Implications:**
- **No Tailscale / residential egress / VPN needed.** From AWS, 5 of 6 sources
  serve 200 with proper headers. (Prior "needs a JS browser / needs residential"
  conclusions were WRONG — they were under-instrumented bare-curl probes.)
- **Every award-source fetcher MUST send the full browser header profile** (add a
  shared header set) — this is the actual requirement, not a browser or a proxy.
- **valor.defense.gov** alone needs residential egress AND has no citation text →
  not worth pursuing; its name-lists aren't needed (Hall of Valor has the
  citations).
- Client library matters too: Python `urllib`/`requests` with full headers worked
  from AWS; keep using the pooled session + full headers.

**Decision:** build direct sources for US (Hall of Valor — done) and UK (London
Gazette + Victoria Cross Online) from AWS with a shared full-header profile; no
bypass infra. Offline/subscription material (WO 373, Fold3, Bundesarchiv) →
`OfflineAwardDataset`.


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



---

## United Kingdom (GBR) — gallantry awards

UK citations are not one free list; the official notices + recommendation files
are online, with some compiled registers. Source map (accessibility verified
2026-10-02):

| Source | Access from our IP | Role |
|---|---|---|
| **London Gazette** (thegazette.co.uk) | **AWS WAF JS-challenge** (`server: CloudFront`, `x-amzn-waf-action: challenge`, 202 + JS page) | Official citation wording, BUT **not reliably fetchable** from a datacenter IP: the first request may pass, then the WAF serves a JS bot-challenge (202/empty for a plain UA; 202 + challenge page for a Chrome UA). Needs a JS-executing browser on a residential IP (the skipped route). robots (when reachable) asks `Crawl-delay: 10` + disallows `/notice/*/data.xml|data.pdf|version/*`. **Deferred** → offline/manual for now. |
| **Victoria Cross Online** (victoriacrossonline.co.uk) | **200, nginx** | VC/GC per-recipient pages with citation. Secondary, VC/GC-focused. |
| TracesOfWar (tracesofwar.com/awards) | **403 (Cloudflare)** | Name index DSO/DCM/CGM/VC; blocked — defer / residential egress. |
| TNA Discovery — **WO 373** (recs for honours/awards 1935-90), WO 390 (DSO reg), WO 391 (DCM reg) | 202 (CloudFront, async/JS) | Original recommendation forms (often fuller than the Gazette); per-record downloads, not a simple fetch → treat as offline/manual → `OfflineAwardDataset`. |
| Fold3 — UK DCM register 1939-45 (~2,117, N&MP), London Gazette WWII military notices (~1.3M) | subscription | Transcribed DCM recommendations + Gazette index. Offline/manual only. |

**Per-award citation availability (what the Gazette actually prints):**
- **VC / GC**: full citations published in the Gazette (+ Victoria Cross Online). Easiest.
- **DSO / DFC / DFM / GM**: short citation usually published in the Gazette.
- **DCM / MM**: often **name/rank/unit only** in the Gazette — the **full story is in the WO 373 recommendation file** (offline). So for DCM/MM, the Gazette gives confirmation + date; the citation TEXT needs WO 373 / the Fold3 DCM register → the offline dataset path.

**Practical lookup order (per owner):** Gazette first → WO 373 on Discovery →
Fold3 (transcribed DCM) if needed.

**Build plan (GB `AwardCitationSource`):** The London Gazette is behind an AWS WAF
JS-challenge (deferred — same residential-egress problem we skipped), so it is NOT
the first direct source. **Victoria Cross Online** (victoriacrossonline.co.uk;
nginx, served cleanly) is the one accessible UK direct source → build it for
VC/GC, gated `nationality == GBR` + award context, provenance recorded. DCM/MM
full text (WO 373 / Fold3 register), TracesOfWar (Cloudflare), and the Gazette
wording → `OfflineAwardDataset`. Same pluggable interface as the US sources.
source (polite: **10s crawl-delay**, cached, descriptive UA, HTML notice page not
the disallowed data.xml/pdf), Victoria Cross Online for VC/GC, gated on
`nationality == GBR` + award context, provenance recorded, preferring the official
Gazette wording. DCM/MM citation text + anything behind Cloudflare/subscription →
`OfflineAwardDataset`. (Same pluggable interface as the US sources.)


---

## Germany (DEU) — gallantry awards

Finding aids are online; some award *rolls* are digitized; the individual
proposals with the citation (*Begründung*) usually are **not**. So for German
awards the open path gives **date + unit**, not the reason — citation text is
mostly a Bundesarchiv-Freiburg request → `OfflineAwardDataset`.

| Source | Access | Role |
|---|---|---|
| **Bundesarchiv invenio** (invenio.bundesarchiv.de, "Suche ohne Anmeldung") | catalog search only (not scan text); some "Digitalisat anzeigen" | Finding aid; search hits the **description**, not the scan text. |
| **RH 7** (OKH Heerespersonalamt, *Orden und Ehrenzeichen*) | partly digitized | *Verleihungslisten* (Iron Cross, by division incl. some Waffen-SS) = **name rolls + dates, NOT the Begründung**; coverage uneven, often only from late 1940/41 (Poland/France thin). RH 7/3030 = Ritterkreuz holders by theater/rank/division (digital copy). RH 7/305 = some War Merit Cross proposal lists **with** a short Begründung. |
| **Pers 6** (personnel files, Freiburg) | **not free online**; request-based; protection period for recent/<10yr-deceased | Often holds a copy of the *Vorschlag* (the citation). Request a named-person search / reproduction from the Bundesarchiv. |
| **Scherzer / Wikipedia Ritterkreuz roll** | web | Confirm the man + date/unit; citation reason still needs Pers 6 / RH 7. |

**Practical path (Knight's Cross citation):** confirm in Scherzer or the Wikipedia
roll → order the Pers 6 file or the relevant RH 7 proposal from Freiburg. Open
digital material gives date/unit more often than the *reason*.

**Build implication:** no accessible direct citation-text source (invenio is
catalog-only; proposals are offline). German award citations route to
`OfflineAwardDataset` (fed from Bundesarchiv reproductions / transcribed RH 7
rolls); an invenio catalog lookup could later confirm date/unit only. Gate on
`nationality == DEU`.

---

## France (FRA) — gallantry awards

French citations are **mostly roll-not-citation**: no complete public roll of
Croix de guerre 1939-45 or Médaille militaire *with citation text*. One accessible
citation-bearing source exists. French-language → citations are **translated to
English** on attach (original + language preserved). Accessibility verified
2026-10-02 (AWS + full headers).

| Source | Access from AWS | Role |
|---|---|---|
| **Ordre de la Libération** (ordredelaliberation.fr/fr/recherche-compagnons) | **200 ✅** | **Direct source.** 1,038 Compagnons, each a biographical notice (decree date, unit/network, services justifying the cross) ≈ citation. Also 18 units + 5 towns. French → translated. BUILT into registry (adapter `ordre_liberation`). |
| Médaille de la Résistance (ordredelaliberation.fr) + Mémoire des Hommes | 200 | Rolls (~65k awards), NOT citation text → confirm date/unit only. |
| france-phaleristique.com (Ordre Libération list) | 200 | Name + date list (no citation). |
| **Base Léonore** (leonore.archives-nationales…) | 200 but **SPA shell (~1.5KB)** | Légion d'honneur dossiers of members deceased <1977; digitized files *may* include the proposal/citation, but it's a JS app + per-dossier open, and not valor-only → offline/dossier. |
| TracesOfWar — Croix de Guerre 1939-45 | 200 (from AWS w/ full headers) | Partial person list, skewed to known Free French/Allied; only some entries carry citation wording. |
| **Croix de guerre 1939-45 / Médaille militaire** | — | **No national online citation index.** Citation was in the *Journal officiel* or a unit *ordre* → Gallica / JO collections (need name + approx date); homologation files at **SHD Vincennes** (on-site/request). → `OfflineAwardDataset`. |

**Allied recipients of French awards:** British recs/citations in TNA **WO 373**;
US awards scattered through unit records + French decrees (no single roll).

**Build:** Ordre de la Libération as the FR direct source (French → translated);
everything else (Croix de guerre, Médaille militaire, Légion d'honneur) →
`OfflineAwardDataset` (JO/Gallica, SHD, Léonore dossiers).
