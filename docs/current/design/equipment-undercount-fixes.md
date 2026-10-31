# Design: equipment undercount fixes — generics, absence, and tri-modal ships

**Status:** DESIGN (2026-10-31). Driven by a gold-standard human review of USA-E-XChannel-8:
~50 real equipment references vs 7 extracted (~7x undercount). Three PRs.

## Governing principle (NEW — applies to ships AND absence)

> **The asserted ACTIVITY / REASON in a mention determines which ENTITY owns the record.
> Identity is SHARED across entities via cross-reference, not duplicated.**

The same real-world thing (a hull, a gun battalion) can produce records in different entities
depending on what the text says it was doing / why it is being mentioned. We do not force one
canonical entity; we let context route it and cross-link the shared identity.

## PR 1 (shipped #649): generics in scope + enemy→theater
- Generic equipment TYPES are in scope (machine gun, artillery, tank, mine, grenade, barbed wire,
  small arms, coastal battery...). Use the type as the name when no model is given.
- "enemy"/"hostile" resolves by theater: ETO→DEU, Med→DEU|ITA (context), Pacific→JPN, CBI→JPN;
  friendly→viewpoint side.

## PR 2: absence / shortage as a first-class fact — WHY is it missing?
A negated/absent equipment mention is real signal ("no supporting artillery", "very few heavy
weapons", "not a single German plane", "two AA gun battalions could not come in until the
following day"). The ASSERTION GATE currently drops all of these (present-only). Add:
- `presence`: present | absent | shortage
- `absence_reason`: battlefield | logistics | unstated  ← the owner-raised distinction
  - **battlefield** = combat/air-superiority/suppression prevented it ("not a single German plane",
    "no supporting artillery" because the supporting arm was destroyed/suppressed). Stays an
    EQUIPMENT presence fact.
  - **logistics** = supply/movement failure prevented it ("AA gun battalions scheduled for D-Day
    could not come in until the following day"). Per the governing principle, this ALSO becomes a
    LOGISTICS record (logistics already models shortage: required/available/shortage) and the two
    cross-reference.
  - **unstated** = absence asserted, cause not given.
- `original_text` MANDATORY (traceability).

## PR 3: transport-capable platforms are MULTI-MODAL (ships AND aircraft)

The governing principle is NOT a ship special-case — it applies to any platform that can both
EMPLOY WEAPONS and MOVE men/cargo. The asserted activity in each mention routes the record; the
SAME airframe/hull can appear in multiple entities across mentions, all cross-referencing a shared
identity.

### Ships (tri-modal)
1. **Weapons platform → EQUIPMENT (naval).** Asserts employing weapons / engaging ("Quincy fired
   its 8-inch guns", "the destroyers shelled the battery"). Named mounted weapons become SEPARATE
   equipment records linked to the ship.
2. **Organization → PEOPLE_GROUP.** Named as a unit / task-force member / crewed org ("Barton",
   "Task Force 124 including Ancon") with NO asserted weapon employment. Do not invent an equipment
   record from an org mention.
3. **Logistics asset → LOGISTICS** (owner-raised): LSTs/LCTs/LCVPs LANDING men/equipment, ships as
   CONVOY members, ships at DOCKS/PIERS UNLOADING cargo — logistics facts, not weapons employment.

### Aircraft (dual-use — owner-raised, SAME principle)
The same airframe routes by asserted activity:
- **Combat / weapons employment → EQUIPMENT (aircraft):** a B-24/B-26 BOMBING, fighter-bombers
  STRAFING, "hostile aircraft" attacking. (The gold list's B-24/B-26/medium bombers, fighters.)
- **Logistics / movement → LOGISTICS:** transport/cargo planes carrying TROOPS (the paratroop
  carriers — "transport planes", "aircraft (inferred paratroop carriers)"), cargo planes hauling
  SUPPLIES, and the RARE case of bombers air-dropping food/equipment resupply. Moving men or cargo
  is a logistics fact even when the airframe is nominally a bomber.
- **Organization → PEOPLE_GROUP:** a named squadron/wing as a unit (vs the aircraft type itself).

DISCRIMINATOR for both classes: ask "what is this platform ASSERTED to be DOING in THIS mention?"
— FIRING/BOMBING/STRAFING = equipment; CARRYING/FERRYING/LANDING/CONVOYING/UNLOADING/AIRDROPPING-
SUPPLIES = logistics; NAMED-AS-A-UNIT (no activity) = people_group. A bomber dropping bombs is
equipment; the same bomber dropping supplies is logistics. A C-47 dropping paratroopers is
logistics; strafing would be equipment.

### TYPE/CLASS is the key attribute, supplemental-resolved when unknown
Ship type (destroyer/cruiser/battleship/LST/LCT/LCVP/APA) and aircraft type/role
(fighter/bomber/transport) are critical. Source MAY state it ("the destroyer Satterlee",
"C-47 transports") or MAY NOT (bare "Barton", "planes"). When unstated, DO NOT guess and DO NOT
drop — flag for SUPPLEMENTAL SEARCH resolution (reuse the existing needs-review / supplemental
enrichment path), so the class is filled from an authority rather than hallucinated.

### Cross-reference
Every platform record (in whichever entity) carries a shared identity key so the equipment /
people_group / logistics facets of the SAME hull/airframe link to each other. Mirrors the existing
equipment↔people_groups `using_unit_name`→`_link_entity` mechanism.

## Open decision (for PR 2/3 confirmation)
- Absence-logistics and ship-logistics both create a record in BOTH the primary entity and
  logistics, cross-linked (option (a)) — consistent with the governing principle. Confirm before
  implementing the dual-write.
