# Siegfried ch.1 Published Works — Phase-3 biblio-note validation reference

Ground truth for validating the HyperWar bibliographical-note **Published Works** matching path
(`src/enrichment/hyperwar_biblio.py` → `biblio_category: published_work`).

Source: https://www.ibiblio.org/hyperwar/USA/USA-E-Siegfried/USA-E-Siegfried-1.html
("THE ROAD TO GERMANY" — the strategic-setting chapter, which cites the high-command memoirs;
the archival-only RoerDams chapters 2a/3a/14a yield `biblio_category: archival` only).

Published works cited in ch.1 body/footnotes (all 6 are in
`_VOLUME_PUBLISHED_WORKS["USA-E-Siegfried"]`):

| # | Author | Title | fn/context |
|---|--------|-------|-----------|
| 1 | Omar N. Bradley | A Soldier's Story | fn 24, 25 |
| 2 | George S. Patton | War As I Knew It | fn 26 |
| 3 | Bernard Montgomery | Normandy to the Baltic | fn 27, body |
| 4 | Francis de Guingand | Operation Victory | body |
| 5 | Dwight D. Eisenhower | Crusade in Europe | body |
| 6 | Charles P. Stacey | The Canadian Army, 1939-1945 | body |

Expected on a Phase-3 run over a corpus containing ch.1: each resolves with
`biblio_category=published_work` + `canonical_title`/`canonical_author`, and (being published
memoirs, not archival records) these are the citations most likely to also resolve to an online
copy via archive.org/Gutenberg.
