# Civilian-document test sources (for the pending civilian-org work)

When civilian/government/political entity extraction + dedup is built (the category
discriminator + alias model — currently a documented GAP), use these as test fixtures:

| Source | Type | Notes |
|---|---|---|
| `unprocesseddocs/176704.pdf` | Local PDF | Civilian document example (owner-provided 2026-10-05). |
| https://www.americanheritage.com/fdr-unites-america-war | Web article | "FDR Unites America for War" — civilian/executive/political entities (White House, administration, Congress, parties). |

These exercise the entities the military unit-key does NOT handle (executive/legislative/
judiciary/party), which need the civilian category-discriminator + alias model — see the
open items in GROUP_DEDUP_RULES_REVIEW.md.
