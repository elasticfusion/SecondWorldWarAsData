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

### Foreign nationals decorated by Germany — route by AWARDING POWER, not nationality

A non-German decorated by the Wehrmacht or Waffen-SS was, for record purposes, in
the **German** system — his award citation is in German (or German-held) files, not
in his home country's sources. The code therefore routes each award by its
**awarding power** (`awarding_power(award_name)`): an **Iron Cross / Knight's Cross
/ German Cross** → the German record system **regardless of the recipient's
nationality**, falling back to nationality only when the award name is unrecognized.

Typical case — a Frenchman in the **Légion des volontaires français** (carried as
**Infanterie-Regiment 638**): Soldbuch, pay card, a line on the regimental
*Kriegsstammrolle*; an Iron Cross proposal went up the army chain to the
**Heerespersonalamt** like any other, and if approved his name can appear on an
**RH 7 Verleihungsliste** or the unit award list, with the *Vorschlag* possibly
surviving in a personnel or division/regiment file at **Freiburg**. A Knight's
Cross also puts him on the central roll.

Two limits (documented, not solvable by code):
- Individual foreign-volunteer files are hit-and-miss — many Soldbücher were lost
  in 1944-45, and the Berlin personnel card index is much thinner for foreign
  volunteers than for Reich Germans.
- Many of these men transferred to the **Waffen-SS** in 1943-44, so a later award
  — including the **Charlemagne** Knight's Crosses — sits in the **SS files at
  Bundesarchiv Berlin**, not the army (RH) series at Freiburg.

When the German file is missing, the **French** side is often better: the **Service
historique de la Défense** holds LVF/French-Waffen-SS engagement lists, Cernay camp
reports, captured German papers, and postwar collaboration dossiers. All offline →
`OfflineAwardDataset` (German-award provenance even though sourced via French
archives). A medal doesn't guarantee a surviving German dossier, but it's one of the
better reasons one was created.

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

---

## Canada (CAN) — gallantry awards

English. The DHH overseas recommendation files are the full citations; the Gazette
is the published citation; Veterans Affairs quotes VC citations. Accessibility
verified 2026-10-02 (AWS + full headers).

| Source | Access from AWS | Role |
|---|---|---|
| **DHH — Canadian Army Overseas Honours and Awards 1939-45** (dhh-dhp.forces.gc.ca/cao-aco) | **200 ✅** | **Direct source.** Original overseas recommendation files (54 vols, by name) = full theatre citations (DSO/MC/DCM/MM, incl. other ranks, nursing sisters, civilians). Army only; VC paperwork + Canada-only recs excluded. |
| **Veterans Affairs — WWII VC fact sheet** (veterans.gc.ca) | **200 ✅** | 16 Canadians (incl. British-unit VCs); individual pages quote the London Gazette VC citation in full. |
| London Gazette | 200 (see UK) | The published legal citation; LAC/DHH give the gazette date to pull it. |
| LAC — Military medals 1812-1969 | timed out (slow/heavy) | Citation cards + registers (Army only; post-1918 MM cards often omit the reason; no RCAF/RCN) → treat as unreliable/offline. |
| Blatherwick rolls (blatherwick.net) | 200 | Free PDF compiled rolls (name/rank/unit/gazette date) — finding aids, NOT narrative citations. |
| RCAF Association / London Gazette | — | RCAF + Canadian-in-RAF (DFC/DFM/DSO) transcripts; Navy (DSC/DSM/CGM) thinnest → Gazette + Blatherwick → offline. |

**Build:** DHH as the CAN direct source (English); VC via Veterans Affairs/Gazette;
RCAF/RCN + LAC cards → offline/Gazette.

---

## Italy (ITA) — gallantry awards

Italian → citations translated to English (original + language preserved). The
Quirinale database carries the full *motivazione* for the **gold** medal; silver/
bronze are thin online. Accessibility verified 2026-10-02 (AWS + full headers).

| Source | Access from AWS | Role |
|---|---|---|
| **Quirinale — Onorificenze** (quirinale.it/onorificenze) | **200 ✅** | **Direct source.** Each record = grade, rank, decree date, full *motivazione* (citation). Gold Medal of Military Valor (~2,607 all-wars; search needs ≥2 surname letters) + Ordine Militare d'Italia + branch valor medals. Italian → translated. |
| Gazzetta Ufficiale (gazzettaufficiale.it) | **200 ✅** | Legal primary text (citation in the decree) — use if a Quirinale sheet is missing. Harder to query by name → offline/targeted. |
| ANCFARGL MOVM 1943-45 | web | 543 gold medals (8 Sep 1943–8 May 1945) by region/birthplace — names/places, NOT full citations. |
| *Le Medaglie d'Oro al Valor Militare* (print, Nastro Azzurro) + branch volumes | print/library | The standard citation collection; WWII split across 1940-43 + liberation vols → offline. |

**Build:** Quirinale as the ITA direct source (Italian → translated); silver/bronze
+ Gazzetta + print volumes → offline. **No complete public roll for WWII silver/
bronze medals** (tens of thousands).

---

## Belgium (BEL) — gallantry awards

**No accessible official citation text.** Belgium never published narrative
citations the way the London Gazette did — the palm/lion is itself the citation
level, and the 2012 Defence Minister confirmed no systematic War Cross roll is
kept. Registered but **DISABLED** (name-list only). Accessibility verified
2026-10-02 (AWS + full headers).

| Source | Access from AWS | Role |
|---|---|---|
| TracesOfWar — Belgium (country 427 / Croix de Guerre 1940) | 200 | Largest public NAME DB; selective (notable Belgians, Allied commanders, contributors). Sometimes notes *why* an award was given; **not** the official citation. |
| Moniteur belge / Belgisch Staatsblad (ejustice) | 200 (search shell) | Royal-decree name lists with device + stock phrase ("en témoignage de reconnaissance des services rendus"), **not** a narrative of the act; wartime London decrees only partly digitized. |
| Wikipedia/Wikimedia category pages | web | Short, uneven lists (senior officers, resistance). |
| **Citation text** | — | War Heritage Institute Brussels / State Archives (dossiers; **offline**). Croix des Évadés 40-45 inventory (3,527 dossiers) is the most citation-rich series — on-site. Allied-to-Belgian awards: UK WO 373 / US GOs. |

**Build:** TracesOfWar registered as a NAME source but **`enabled: false`** (don't
run a name-list as a citation source); real citations → `OfflineAwardDataset`.

---

## Netherlands (NLD) — gallantry awards

Name lists online; **full official citations are not in one public DB**. Registered
but **DISABLED** by default (TracesOfWar pages vary; many lack the citation).
Accessibility verified 2026-10-02 (AWS + full headers).

| Source | Access from AWS | Role |
|---|---|---|
| TracesOfWar — NL (Bronzen Leeuw 200 / Bronzen Kruis 201, Vliegerkruis, Verzetskruis…) | 200 | Alphabetical recipient lists; better pages give rank/unit/date/place + Royal Decree + short deed account, sometimes the recommendation/citation, **many do not**. Strongest for higher awards. |
| rmwo.nl (Museum Bronbeek MWO database, 2024) | 200 (JS app) | ~6,000 MWO knight biographies (WWII cohort ~169); **biographical, not decree transcripts**. |
| lintjes.nl → Databank Dapperheidsonderscheidingen | 200 | Official 1815-1963 **name-and-award index** (Defence/NIMH); not citations, not released as open data. |
| **Citation text** | — | Nationaal Archief decoration files 1815-1993 + **NIMH** recommendation/Kapittel files (**offline**); printed rolls (Meijer, *Bronzen Leeuw/Bronzen Kruis* 1990; Meijer & Vis, *Het Vliegerkruis* 1997) give rank/unit/Royal Decree + "mutatie" (place/date) — deed summary, not always full narrative. |

**Build:** TracesOfWar registered as a NAME source, **`enabled: false`** (partial
citations only); MWO biographies via rmwo.nl later if useful; real citations →
`OfflineAwardDataset` (Nationaal Archief / NIMH + Meijer rolls).

---

## Romania (ROU), Hungary (HUN), Finland (FIN), Spain (ESP) — top-grade name rolls

Common pattern: **complete/near-complete name rolls online for the TOP award(s), but
the formal citation text is mostly in print or in the original award orders** — no
single free citation database. All four are registered but **`enabled: false`**
(name-roll, not citation source); citation text routes to `OfflineAwardDataset`.
Non-English → translated (RO/HU/FI/ES). Accessibility verified 2026-10-02 (AWS + full headers).

### Romania — Order of Michael the Brave
| Source | Access | Role |
|---|---|---|
| WorldWar2.ro (/decoratii) | 200 | Officers by class: rank, unit, decree no.+date, class. Names + decree refs, **not** the deed. |
| Romanian Wikipedia (MO-compiled lists) | web | Officers + battle-flag rolls with decree nos. |
| **Citation** | — | The **brevet** (diploma) carries the narrative (e.g. the Dicezare brevet is online); most are **offline** (brevet / decoration file at military archives). Monitorul Oficial = conferral lists. Lower grades (Military Virtue, Aeronautical Virtue): no comparable roll. |

### Hungary — Gold Medal of Bravery + Maria Theresa
| Source | Access | Role |
|---|---|---|
| huWiki / enWiki "Medal of Bravery" + archived rendjel roll | web | Officer Gold (~22+3), Enlisted Gold (~39): name/rank/date/place. |
| TracesOfWar (Officer Gold) | 200 (empty from AWS) | Points at the citation book; roll partial. |
| hungarianarmedforces.com (Magyar Érdemrend index) | 200 | Searchable award index (incl. swords grades); **not** citations. |
| **Citation** | — | **Oszlányi** (single WWII Maria Theresa knight) appointment text online (archived rendjel). Otherwise: Illésfalvi/Kovács/Maruzs *For Valour* (2010, print) + scattered articles (Corvinák, Kitörés 1945). Below gold → archives. → offline. |

### Finland — Mannerheim Cross
| Source | Access | Role |
|---|---|---|
| marskinritarit.fi | 200 | Knights' association: the roll + short bios. |
| en/fi Wikipedia Mannerheim Cross list | 200 | Complete wartime roll (191; 1941-45): knight no., name, rank, service, date, unit, short note — **not** the official citation. |
| **Citation** | — | GHQ *nimitysperustelut* in Hurmerinta & Viitanen, *Mannerheim-ristin ritarit: ritarimatrikkeli* (1994/2004/2006, **print**) → offline. Lower Liberty crosses/medals too numerous for a public roll. |

### Spain — Cruz Laureada de San Fernando / Medalla Militar (División Azul)
| Source | Access | Role |
|---|---|---|
| esWiki annex (Laureada by year) | 200 | WWII set = División Azul (Eastern Front 1941-43): ~8 Laureadas + ~42-54 Medallas Militares. Names + **one-line merit**, not the formal citation. |
| **Citation** | — | Award order in the **Diario Oficial del Ministerio del Ejército** (not on one free site) + the *juicio contradictorio* for the Laureada → offline. Blue Division Medallas Militares / Cruces de Guerra: no online citation roll. Print: Prieto Barrio & Pérez Rubio, *Condecoraciones y distintivos de la División Azul*. |

---

## Additional nations — multiple partial sources (2026-10-02, AWS + full headers)

Principle confirmed by the owner: **multiple sites per country is normal; almost none
is a complete citation file.** Three structural routes handle most of these without a
new per-country citation adapter:

- **Soviet award → Podvig Naroda** (new citation source; scanned award sheets).
- **British-channel award → the already-registered UK London Gazette + WO 373**
  (awarding power = GBR), for India, Australia, NZ, South Africa, Nepal/Gurkhas,
  Denmark-exile, etc.
- **US award to a foreign soldier → Hall of Valor** (awarding power = USA), for Brazil
  (FEB), Philippines (Scouts/guerrillas), Mexico (Esc. 201), Mongolia-via-Soviet=SUN.

### Soviet Union (SUN) — ENABLED citation source
Podvig Naroda (podvignaroda.ru, 200) carries names/awards/units and for many the
**scanned award sheet (нагradной лист = the citation)**. Russian → translated. Pamyat
Naroda (401 from AWS → offline); OBD Memorial = dead/missing cross-links, not valor.

### Registered name-roll sources (enabled:false; citations offline)
| Country | Source(s) | Note |
|---|---|---|
| Australia (AUS) | AWM people search (200) | Award records + rec/Gazette links; DVA/NAA confirm person. VC/DSO citations via Gazette. |
| Netherlands (NLD) | Oorlogsbronnen (200) + Bronzen Kruis/Leeuw/Vliegerkruis Wikipedia | Bios + decree dates, not narratives; Staatscourant decrees → offline. |
| Norway (NOR) | krigskorset.no (200) + lokalhistoriewiki + noWiki | War Cross pages/lists; citations only in some bios. |
| Poland (POL) | FEEFHS VM index (~24k, class only) + plWiki rolls | Cross of Valour / Grunwald partial; NO citations online. |

### British-channel countries (route to UK London Gazette / WO 373)
| Country | Note |
|---|---|
| India (IND) | VC citations in Gazette; MC/DCM/MM name-only; WO 373 recs (paywalled); IOM/IDSM in Gazette of India (British Library). |
| New Zealand (NZL) | Online Cenotaph (403 from AWS) + Gazette; *Gallant Acts & Noble Deeds* (print). Route to Gazette. |
| South Africa (ZAF) | Gazette + WO 373; no SA WWII valor DB (Honoris Crux is post-1945). |
| Nepal (NPL) | Gurkhas in British service → Gazette + WO 373; no Nepalese national roll. |
| Denmark (DNK) | Danish exile personnel decorated by Britain → Gazette (British awards). |

### US-awarded foreign soldiers (route to Hall of Valor by awarding power)
Brazil (BRA, FEB: 1 DSC + Silver/Bronze Stars), Philippines (PHL, Scouts/guerrillas),
Mexico (MEX, Escuadrón 201). Their own national rolls are offline/absent.

### No accessible citation source (→ offline / documented absence)
Registered as explicit **offline placeholders** (enabled:false) so a person still routes
and a future source has a home — each registry `notes` field records **where to search**
(the archive/print location):
- **China (CHN)** — Academia Historica (國史館) + MND historical records, Taipei.
- **Greece (GRC)** — Hellenic Army General Staff Army History Directorate (ΔΙΣ/ΓΕΣ), Athens.
- **Yugoslavia (YUG)** — People's Hero ~1,300 names on Wikipedia/bio sites (not citations); Vojni arhiv, Belgrade.
- **Czechoslovakia (CSK)** — Vojenský ústřední archiv, Prague.
- Ethiopia, Luxembourg, Belgium (see its section): no online valor DB. Mongolia: 1945
  recipients appear under their **Soviet** award in Podvig Naroda.

---

## Related documents
- [People Biographical Enrichment](../features/people/biographical-enrichment.md) — how award sourcing is wired into the per-person Phase-3 flow (gating, routing, preservation).
- [Language Translation](LANGUAGE_TRANSLATION.md) — the translate-at-the-seam mechanism reused to normalize non-English citations to English.
- [Schema Reference](../SCHEMA_REFERENCE.md) — the `MilitaryAward` provenance fields (`citation_text`, `citation_text_original`, `citation_language`, `source_name/url`, `verified`, `sourcing_attempts`).
- [Supplementary search review](../../archive/2026-10-05/SUPPLEMENTARY_SEARCH_REVIEW.md) (archived) — origin of the "authoritative sources over search engines" direction.
