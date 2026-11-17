# Equipment Designation Systems (US vs. German) — reference for dedup & identity

Authoritative reference for how WWII equipment was *named*, and why that matters for
deduplication, the alias table, and `_identity_scope`. The core problem: **US and
German designation systems are structurally different**, and German equipment carried
**several overlapping identifiers at once** — which will wrongly split records or fail to
match unless dedup/aliases account for it.

## US system (relatively simple — one catalog)

- Official form: *Tank, Medium, M4*. The **`M`-number** is an ordnance model number within
  a class; production changes add an **A-suffix**: `M4A1`, `M4A3`.
- **"Sherman" was a British name** adopted in Allied use — **not** the US type designation.
  (This is why the alias table maps the nickname `sherman → m4 sherman`.)
- Closest to a single stable identity: the `M`-number (`technical_identifier`).

## British system (layered — A-number vs. service name vs. census number)

Britain used **no single running "M" series**. Three different layers, none a catalog:

1. **A-number** (closest to "M4"): General Staff spec, sequential, `A`-prefixed; prototypes
   add `E`. `A12` = Infantry Tank Mk II (Matilda II); `A15` = Cruiser Mk VI (Crusader);
   `A22` = Infantry Tank Mk IV (Churchill); `A27L` = Centaur / `A27M` = Cromwell; `A34` =
   Comet. Private-venture tanks (Valentine) often had **no A-number**.
2. **Full service title** stacks type + class mark + name + vehicle mark:
   `Tank, Infantry, Mk IV, Churchill Mk VII` (a double-mark system — class mark AND vehicle
   mark).
3. **War Department census number**: letter + digits painted on the hull (`T` tanks,
   `F` armoured cars, `L` lorries) — identifies the **individual vehicle, not the model**.

**US tanks in British service were renamed + re-marked** — a direct dedup hazard:
- `M4 → Sherman I`, `M4A1 → Sherman II`, `M4A2 → Sherman III`, …
- A letter after the mark = gun/suspension change: **`C`** = 17-pounder (the **Firefly** was
  officially `Sherman IC`/`VC`, not "Firefly"), **`A`** = 76 mm, **`Y`** = HVSS.
- So a US doc's `Medium Tank, M4` and a British doc's `Sherman V` are the **SAME type**.

**Guns**: role + projectile weight/calibre + mark — `Ordnance QF 6-pounder`,
`QF 17-pounder`, `QF 25-pounder`, `BL 5.5-inch`. **Small arms** use `No.`/`Mk` —
`Rifle, No. 4 Mk I`; `Machine Carbine, Sten, Mk II` — not an M-number.

## German system (descriptive + fragmented — multiple identifiers at once)

One vehicle can **correctly** be called **all** of these simultaneously:
`Panzer IV` = `Pz.Kpfw. IV Ausf. H` = `Sd.Kfz. 161/2`.

**Tanks / armored vehicles** stack three things:
1. **Type name**: `Panzerkampfwagen` + Roman numeral — `Pz.Kpfw. IV`, `Pz.Kpfw. V Panther`,
   `Pz.Kpfw. VI Tiger`.
2. **Variant**: `Ausführung` (abbr. `Ausf.`) + letter — `Pz.Kpfw. IV Ausf. H`,
   `Panther Ausf. G`. Letters are **not always alphabetical/sequential**: on the Tiger,
   `Ausf. H` = Henschel, `Ausf. P` = Porsche.
3. **Inventory number**: `Sonderkraftfahrzeug` (`Sd.Kfz.`) assigned by the
   Heereswaffenamt. Grouped by **role**, not year: ~100–199 tanks/SP guns, 200–299 recon /
   carriers. Examples:
   - Tiger I = **Sd.Kfz. 181**
   - Panther = **Sd.Kfz. 171**
   - Panzer IV (short 7.5 cm) = **Sd.Kfz. 161**; long-gun = **161/1**, **161/2**
   - armored half-track = **Sd.Kfz. 251**

**Guns / small arms** — caliber + type + model year/sequence:
`7.5 cm KwK 40 L/48` (tank gun), `7.5 cm PaK 40` (anti-tank), `8.8 cm FlaK 36`,
`MG 34` / `MG 42`, `Karabiner 98k`, `MP 40`, `Sturmgewehr 44`.
**Captured** foreign weapons keep the form + a nationality letter in parentheses:
`7.5 cm PaK 97/38(f)` (French), `(r)` = Russian. (Note: this parallels our per-mention
`captured` flag — the *(f)/(r)* marks origin, not operator.)

## Implications for THIS pipeline

### Identity (`_identity_scope` / `_is_enrichment_target`, enrichment)
- `Pz.Kpfw. VI Tiger`, `Tiger`, `Sd.Kfz. 181` are all **specific** identities for the same
  type — all should enrich (to the canonical German record), none are generic.
- `Ausf. H` / `Ausf. G` are **variants** (like M4A1/M4A3) → inline `variants[]`, not
  separate records.

### Alias table (`config/equipment_aliases.yaml`)
- Nicknames already map (`tiger → pzkpfw vi tiger`, correct — German, not a Sherman).
- `Sd.Kfz.` numbers are aliased for 181 (Tiger), 171 (Panther), 161/161-1/161-2 (Panzer IV),
  141, 142, 251 and 234. Other Sd.Kfz. numbers are not, and fall through to the Grok resolver.
- `_normalize_designation` (`src/extraction/equipment.py`) unifies `Pz.Kpfw.` / `PzKpfw` /
  `Panzerkampfwagen` to `panzer` and strips `Sd.Kfz.` punctuation before alias lookup.
  Roman-numeral handling on the equipment side is not implemented.

### Dedup (`src/dedup/find_duplicate_equipment.py`)
- The leading-number veto (105 vs 155) is US-caliber-oriented; it must not wrongly veto
  German `Sd.Kfz.` numbers vs type Roman numerals.
- Needs German-aware normalization: `Pz.Kpfw. IV` == `Panzer IV` == `PzKpfw IV`;
  `Ausf.`-letter as a variant discriminator; `Sd.Kfz. 161/2` as an inventory id that maps
  to the same type as `Sd.Kfz. 161`.
- Captured `(f)`/`(r)` suffix marks **origin**, consistent with the origin-vs-operator
  model (see EQUIPMENT_DEDUPLICATION.md) — it must not split a captured-use mention from
  the origin record.

### British (same hazards, different shape)
- **US-tank-in-British-service renames are a split hazard:** `M4` (US doc) and `Sherman V`
  (British doc) are the SAME type. The alias table maps Sherman I-V to the M4 family
  (`sherman i` → M4, `sherman iii` → M4A2, …), A-numbers (A11, A12, A15, A22, A27L/M, A34),
  and the service names Churchill/Cromwell/Crusader/Matilda/Comet/Centaur/Valentine.
- **`Firefly` is officially `Sherman IC`/`VC`** (17-pdr Sherman); the table maps `firefly`,
  `sherman ic` and `sherman vc` to `sherman firefly`.
- British **census numbers** (`T`/`F`/`L` + digits) identify an **individual vehicle, not
  the model** — must NEVER be used as a type identity (would fragment records per vehicle).
- Mark letters: `C`=17-pdr, `A`=76 mm, `Y`=HVSS — these are **variant** discriminators.

### Runtime Grok disambiguator (implemented)
Hand-maintaining full cross-walks across three multi-layer systems always leaves gaps, so
`src/extraction/equipment_disambiguation.py::resolve_designation` resolves a raw designation
to `{canonical_name, nationality_of_origin, equivalents[]}` (canonicalization, not a claim
about the source). Order: exact / curated alias → learned alias
(`config/equipment_aliases_learned.yaml`, first-wins) → fuzzy (`SequenceMatcher ≥ 0.90`) →
Grok (`prompts/equipment_disambiguation.yaml`, cached, one call per distinct designation).
The tier is stamped as `identity_source`; Grok results are also appended to
`output/equipment/disambiguation_suggestions.jsonl` for human promotion into the curated
YAML. Gated by env `EQUIPMENT_DISAMBIGUATION` (default true); fails open to the raw name.
See [EQUIPMENT_FLOW.md](EQUIPMENT_FLOW.md) Stage 3.

## Status

Reference, plus the implemented disambiguator above. Still open: dedup German-aware
normalization (`Ausf.` letter as variant discriminator, `Sd.Kfz. 161/2` vs `161`) beyond the
alias entries, and Japan/Italy/Soviet alias sections.
