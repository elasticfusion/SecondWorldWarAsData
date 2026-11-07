# Copyright reference tables

Country-level copyright rules behind the `license` / `copyright_status` provenance fields on
bibliography and supplemental records. Moved from `contextmanagement/Specs/` on 2026-11-06.

| File | Covers |
|---|---|
| `COPYRIGHT_BY_COUNTRY_BOOKS.md` | Governing law, protected works, term, registration, moral rights, exceptions (US, Canada, UK, France, Germany) |
| `COPYRIGHT_BY_COUNTRY_PERIODICALS.md` | Who owns copyright in periodical and journal contributions, same five countries |

**Where the code applies these rules**

- `src/extraction/copyright_calculator.py`: `COPYRIGHT_DURATION` (life + 70 for the same five countries,
  as ISO-3 codes) and `calculate_copyright_expiration`.
- `src/extraction/supplemental_advanced.py`: `determine_copyright_status` / `_determine_usa_copyright`
  (US: published before 1928, 95 years from publication for 1928–1977, life + 70 after 1977).

**Caveats**

- These tables are unsourced summaries, not legal advice. Check a primary source before relying on a
  determination.
- Known code issues are tracked in `docs/current/TODO.md`, under the "copyright date rules" item.
