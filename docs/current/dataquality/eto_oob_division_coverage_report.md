# ETO Order of Battle — Division Coverage Report

Comparison of extracted CSV coverage against the source PDF (`ETO_Order_of_Battle.pdf`).

## Summary

- **ETO volume divisions (canonical, excl. 1st Armored):** 60
- **TOC divisions parsed:** 37
- **PDF sections identified (headers/insignia):** 59
- **Unique division labels in CSVs:** 60
- **Divisions with at least one CSV row:** 60
- **Divisions present in all eight CSV types:** 14
- **Divisions in TOC but with zero CSV rows:** 1
- **Divisions with partial CSV coverage:** 46

### Key findings

- **28th Infantry Division** is listed in the TOC (printed pp. 109–119) but the scanned PDF
  jumps from p. 108 to p. 120; no extractable section exists in this file.
- **71st Infantry Division** insignia is at printed p. 224 (pdf 233), but printed
  pp. 225–226 are missing from the scan; attachments resume at pdf 234. Command &
  staff, statistics, campaigns, and organic units for the 71st cannot be extracted.
- **`1do3th Armored Division`** is a spurious CSV label (1 attachment row); the source page
  (pdf 544) is **13th Armored Division** — OCR `103d` → `1do3d`.
- **13th Airborne Division** (Black Cat; TOC printed p. 88) starts at **pdf 108**
  (insignia) through pdf 113. The volume lists no separate 13th Infantry Division.
- Pdf 305–306 headers OCR as `13d Infantry` but are **83d Infantry** command-post
  continuations (page overrides applied).
- **1st Armored Division** is in the canonical list but is Mediterranean-only and not part of
  this ETO volume body.

### CSV anomalies

- `1do3th Armored Division` — rows in: eto_oob_attachments.csv

### Divisions in TOC but absent from all CSVs

- 28th Infantry Division

## Per-division coverage

| Division | PDF | TOC | Cmd Staff | Stats | Campaigns | Organic | Attach | Detach | Higher | Cmd Posts | Notes |
|---|:---:|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1st Infantry Division | Y | N | 48 | 10 | 4 | 19 | 148 | 27 | 15 | 20 |  |
| 2d Infantry Division | Y | N | — | 10 | 5 | 18 | 110 | 34 | 21 | — | Absent from: Command & Staff, Command Posts |
| 3d Infantry Division | Y | Y | 12 | 11 | 3 | 101 | 19 | 14 | 47 | — | Absent from: Command Posts |
| 4th Infantry Division | Y | Y | 46 | 13 | 4 | 18 | 183 | 54 | 18 | 5 |  |
| 5th Infantry Division | Y | Y | 14 | 16 | 5 | 18 | 84 | 46 | — | 14 | Absent from: Higher Units |
| 9th Infantry Division | Y | N | 30 | 12 | 2 | 18 | 101 | 76 | 10 | 34 |  |
| 26th Infantry Division | Y | N | 30 | 10 | 4 | 61 | — | 9 | 19 | — | Absent from: Attachments, Command Posts; No attachments recorded |
| 28th Infantry Division | — | Y | — | — | — | — | — | — | — | — | Printed pp. 109–119 missing from scan; Printed page footers jump from 108 to 120 (pdf 128 to 129). The full 28th Infantry Division section is not present in this PDF file. |
| 29th Infantry Division | Y | N | 3 | 5 | 4 | 18 | 108 | 15 | 10 | — | Absent from: Command Posts |
| 30th Infantry Division | Y | Y | 4 | 7 | 3 | 18 | 133 | 33 | 17 | 25 |  |
| 35th Infantry Division | Y | Y | 1 | 6 | 5 | 18 | 73 | 140 | 18 | — | Absent from: Command Posts |
| 36th Infantry Division | Y | Y | 5 | 6 | 3 | 17 | 117 | 11 | — | 23 | Absent from: Higher Units |
| 42d Infantry Division | Y | Y | 8 | 13 | 1 | 18 | 32 | 18 | 4 | 17 |  |
| 44th Infantry Division | Y | Y | 7 | 9 | 3 | 18 | 41 | 12 | — | 35 | Absent from: Higher Units |
| 45th Infantry Division | Y | N | 4 | 8 | 3 | 18 | 73 | 18 | 8 | 34 |  |
| 63d Infantry Division | Y | Y | 1 | 6 | 1 | 18 | 37 | 9 | 9 | — | Absent from: Command Posts |
| 65th Infantry Division | Y | Y | 3 | 10 | 2 | 18 | 36 | — | — | — | Absent from: Detachments, Higher Units, Command Posts; No detachments recorded |
| 66th Infantry Division | Y | Y | — | 8 | — | 18 | 53 | 4 | 2 | — | Absent from: Command & Staff, Campaigns, Command Posts |
| 69th Infantry Division | Y | Y | — | 10 | 2 | 18 | 22 | 22 | — | — | Absent from: Command & Staff, Higher Units, Command Posts |
| 70th Infantry Division | Y | Y | 1 | 18 | 2 | 18 | 36 | 9 | — | — | Absent from: Higher Units, Command Posts |
| 71st Infantry Division | Y | Y | — | — | — | — | 15 | 6 | — | — | Printed pp. 225–226 missing from scan; Insignia at printed p. 224 (pdf 233). Command & staff, statistics, campaigns, and organic units are absent; the scan resumes at pdf 234 with attachments.; Absent from: Command & Staff, Statistics, Campaigns, Organic Units, Higher Units, Command Posts |
| 75th Infantry Division | Y | Y | 13 | 13 | 3 | 18 | 79 | — | 14 | — | Absent from: Detachments, Command Posts; No detachments recorded |
| 76th Infantry Division | Y | Y | — | 12 | 3 | 18 | 27 | 14 | 8 | 26 | Absent from: Command & Staff |
| 78th Infantry Division | Y | Y | 1 | 12 | 3 | 18 | 60 | 5 | 10 | 20 |  |
| 79th Infantry Division | Y | N | 3 | 3 | — | 18 | 76 | — | 20 | 30 | Absent from: Campaigns, Detachments; No detachments recorded |
| 80th Infantry Division | Y | Y | — | 7 | 2 | 18 | 31 | 20 | 2 | 26 | Absent from: Command & Staff |
| 83d Infantry Division | Y | N | 2 | 7 | 4 | 18 | 194 | 24 | — | 36 | Absent from: Higher Units |
| 84th Infantry Division | Y | Y | 12 | 10 | 2 | 18 | 47 | 9 | 7 | 21 |  |
| 86th Infantry Division | Y | N | — | 3 | 1 | 18 | 25 | 11 | 1 | — | Absent from: Command & Staff, Command Posts |
| 87th Infantry Division | Y | Y | — | 8 | 3 | 18 | 39 | 6 | — | — | Absent from: Command & Staff, Higher Units, Command Posts |
| 89th Infantry Division | Y | N | — | 7 | 1 | 18 | 7 | 6 | 2 | 17 | Absent from: Command & Staff |
| 90th Infantry Division | Y | Y | — | 7 | 3 | — | 69 | 17 | 11 | — | Absent from: Command & Staff, Organic Units, Command Posts |
| 94th Infantry Division | Y | Y | 3 | 14 | 3 | 17 | 45 | 18 | 7 | 11 |  |
| 95th Infantry Division | Y | Y | — | 21 | 1 | — | — | — | 11 | — | Absent from: Command & Staff, Organic Units, Attachments, Detachments, Command Posts; No attachments recorded; No detachments recorded |
| 97th Infantry Division | Y | N | — | 5 | — | 21 | — | 4 | 9 | — | Absent from: Command & Staff, Campaigns, Attachments, Command Posts; No attachments recorded |
| 99th Infantry Division | Y | Y | 6 | 11 | 2 | 21 | 37 | 36 | — | 10 | Absent from: Higher Units |
| 100th Infantry Division | Y | Y | 24 | 21 | 6 | 93 | 55 | 14 | — | 24 | Absent from: Higher Units |
| 102d Infantry Division | Y | Y | 1 | 21 | 3 | 18 | 111 | 145 | — | 3 | Absent from: Higher Units |
| 103d Infantry Division | Y | Y | — | — | — | — | — | — | 9 | — | Absent from: Command & Staff, Statistics, Campaigns, Organic Units, Attachments, Detachments, Command Posts; No attachments recorded; No detachments recorded |
| 104th Infantry Division | Y | N | — | 11 | 3 | 62 | — | 16 | 9 | — | Absent from: Command & Staff, Attachments, Command Posts; No attachments recorded |
| 106th Infantry Division | Y | Y | — | 12 | 2 | 22 | 22 | 16 | — | — | Absent from: Command & Staff, Higher Units, Command Posts |
| 1do3th Armored Division | N | N | — | — | — | — | 1 | — | — | — | No identifiable PDF section header/insignia page; Non-canonical division label in CSV; Absent from: Command & Staff, Statistics, Campaigns, Organic Units, Detachments, Higher Units, Command Posts; No detachments recorded |
| 2d Armored Division | Y | N | 1 | 10 | 5 | 20 | 69 | 49 | 17 | 8 |  |
| 3d Armored Division | Y | N | 11 | 9 | 5 | 19 | 82 | 392 | 6 | 12 |  |
| 4th Armored Division | Y | N | 1 | — | — | 45 | 63 | 26 | 15 | — | Absent from: Statistics, Campaigns, Command Posts |
| 5th Armored Division | Y | Y | 3 | 16 | 8 | 91 | 5 | 40 | 20 | 1 |  |
| 6th Armored Division | Y | Y | — | — | — | — | 39 | 34 | 15 | — | Absent from: Command & Staff, Statistics, Campaigns, Organic Units, Command Posts |
| 7th Armored Division | Y | Y | 3 | 12 | 4 | 76 | — | 120 | — | 4 | Absent from: Attachments, Higher Units; No attachments recorded |
| 8th Armored Division | Y | Y | 1 | 7 | 2 | — | 21 | 20 | 10 | — | Absent from: Organic Units, Command Posts |
| 9th Armored Division | Y | N | 1 | 14 | 3 | 17 | 34 | 35 | 17 | — | Absent from: Command Posts |
| 10th Armored Division | Y | N | 3 | 16 | 3 | 22 | 39 | 10 | 15 | 6 |  |
| 11th Armored Division | Y | N | 1 | 9 | 2 | 16 | 13 | 18 | 14 | 24 |  |
| 12th Armored Division | Y | Y | — | 12 | 1 | 16 | 22 | 56 | — | — | Absent from: Command & Staff, Higher Units, Command Posts |
| 13th Armored Division | Y | N | — | — | — | 32 | 18 | 4 | — | — | Absent from: Command & Staff, Statistics, Campaigns, Higher Units, Command Posts |
| 14th Armored Division | Y | Y | 11 | 6 | 1 | — | 13 | 194 | — | — | Absent from: Organic Units, Higher Units, Command Posts |
| 16th Armored Division | Y | N | 1 | 4 | — | 21 | — | — | 4 | 3 | Absent from: Campaigns, Attachments, Detachments; No attachments recorded; No detachments recorded |
| 20th Armored Division | Y | Y | 1 | 7 | 1 | 21 | 4 | 8 | 12 | — | Absent from: Command Posts |
| 13th Airborne Division | Y | N | 1 | 1 | 1 | 19 | — | — | 3 | 2 | Absent from: Attachments, Detachments; No attachments recorded; No detachments recorded |
| 17th Airborne Division | Y | Y | 1 | 11 | 3 | — | 8 | 12 | 14 | — | Absent from: Organic Units, Command Posts |
| 82d Airborne Division | Y | N | 16 | — | — | 139 | 24 | 12 | 15 | — | Absent from: Statistics, Campaigns, Command Posts |
| 101st Airborne Division | Y | N | — | — | — | 65 | 13 | 14 | 23 | — | Absent from: Command & Staff, Statistics, Campaigns, Command Posts |

## Divisions with no attachments

- 26th Infantry Division
- 95th Infantry Division
- 97th Infantry Division
- 103d Infantry Division
- 104th Infantry Division
- 7th Armored Division
- 16th Armored Division
- 13th Airborne Division

## Divisions with no detachments

- 65th Infantry Division
- 75th Infantry Division
- 79th Infantry Division
- 95th Infantry Division
- 103d Infantry Division
- 1do3th Armored Division
- 16th Armored Division
- 13th Airborne Division

## Divisions with complete CSV coverage (all 8 files)

- 1st Infantry Division
- 4th Infantry Division
- 9th Infantry Division
- 30th Infantry Division
- 42d Infantry Division
- 45th Infantry Division
- 78th Infantry Division
- 84th Infantry Division
- 94th Infantry Division
- 2d Armored Division
- 3d Armored Division
- 5th Armored Division
- 10th Armored Division
- 11th Armored Division

## PDF page ranges (header/insignia hits)

- **1st Infantry Division:** pdf 22–34 (9 header/insignia hits)
- **2d Infantry Division:** pdf 37–405 (10 header/insignia hits)
- **3d Infantry Division:** pdf 47–302 (8 header/insignia hits)
- **4th Infantry Division:** pdf 59–72 (8 header/insignia hits)
- **5th Infantry Division:** pdf 75–83 (7 header/insignia hits)
- **9th Infantry Division:** pdf 94–107 (9 header/insignia hits)
- **26th Infantry Division:** pdf 121–128 (7 header/insignia hits)
- **29th Infantry Division:** pdf 130–136 (5 header/insignia hits)
- **30th Infantry Division:** pdf 142–151 (7 header/insignia hits)
- **35th Infantry Division:** pdf 153–161 (6 header/insignia hits)
- **36th Infantry Division:** pdf 163–170 (5 header/insignia hits)
- **42d Infantry Division:** pdf 172–178 (7 header/insignia hits)
- **44th Infantry Division:** pdf 180–186 (6 header/insignia hits)
- **45th Infantry Division:** pdf 188–196 (7 header/insignia hits)
- **63d Infantry Division:** pdf 198–204 (7 header/insignia hits)
- **65th Infantry Division:** pdf 205–209 (5 header/insignia hits)
- **66th Infantry Division:** pdf 214–218 (4 header/insignia hits)
- **69th Infantry Division:** pdf 220–225 (4 header/insignia hits)
- **70th Infantry Division:** pdf 227–228 (2 header/insignia hits)
- **71st Infantry Division:** pdf 234–236 (3 header/insignia hits)
- **75th Infantry Division:** pdf 239–245 (4 header/insignia hits)
- **76th Infantry Division:** pdf 248–254 (6 header/insignia hits)
- **78th Infantry Division:** pdf 257–262 (3 header/insignia hits)
- **79th Infantry Division:** pdf 266–274 (5 header/insignia hits)
- **80th Infantry Division:** pdf 276–281 (5 header/insignia hits)
- **83d Infantry Division:** pdf 297–307 (4 header/insignia hits)
- **84th Infantry Division:** pdf 309–314 (2 header/insignia hits)
- **86th Infantry Division:** pdf 319–323 (5 header/insignia hits)
- **87th Infantry Division:** pdf 325–330 (5 header/insignia hits)
- **89th Infantry Division:** pdf 333–337 (5 header/insignia hits)
- **90th Infantry Division:** pdf 339–350 (8 header/insignia hits)
- **94th Infantry Division:** pdf 352–357 (6 header/insignia hits)
- **95th Infantry Division:** pdf 359–363 (4 header/insignia hits)
- **97th Infantry Division:** pdf 366–370 (5 header/insignia hits)
- **99th Infantry Division:** pdf 373–379 (6 header/insignia hits)
- **100th Infantry Division:** pdf 381–387 (4 header/insignia hits)
- **102d Infantry Division:** pdf 400–406 (6 header/insignia hits)
- **103d Infantry Division:** pdf 413–416 (2 header/insignia hits)
- **104th Infantry Division:** pdf 419–424 (5 header/insignia hits)
- **106th Infantry Division:** pdf 426–431 (5 header/insignia hits)
- **2d Armored Division:** pdf 433–441 (8 header/insignia hits)
- **3d Armored Division:** pdf 443–454 (8 header/insignia hits)
- **4th Armored Division:** pdf 456–465 (9 header/insignia hits)
- **5th Armored Division:** pdf 468–475 (7 header/insignia hits)
- **6th Armored Division:** pdf 480–556 (7 header/insignia hits)
- **7th Armored Division:** pdf 488–496 (7 header/insignia hits)
- **8th Armored Division:** pdf 498–503 (5 header/insignia hits)
- **9th Armored Division:** pdf 506–512 (6 header/insignia hits)
- **10th Armored Division:** pdf 514–520 (6 header/insignia hits)
- **11th Armored Division:** pdf 522–525 (4 header/insignia hits)
- **12th Armored Division:** pdf 529–535 (7 header/insignia hits)
- **13th Armored Division:** pdf 539–543 (5 header/insignia hits)
- **14th Armored Division:** pdf 545–550 (5 header/insignia hits)
- **16th Armored Division:** pdf 554–558 (4 header/insignia hits)
- **20th Armored Division:** pdf 560–564 (5 header/insignia hits)
- **13th Airborne Division:** pdf 110–113 (2 header/insignia hits)
- **17th Airborne Division:** pdf 115–119 (5 header/insignia hits)
- **82d Airborne Division:** pdf 289–293 (4 header/insignia hits)
- **101st Airborne Division:** pdf 393–398 (6 header/insignia hits)

## Method

- **CSV side:** unique `division` values and row counts per output file.
- **PDF side (independent):** TOC parse from pdf pages 15–18; per-page header OCR
  (`match_division`) and spaced insignia title pages through the division section
  (before ORGANIC COMPOSITION). Does not use CSV carry-forward logic.
- **Missing scan:** `config/eto_oob_pdf_page_division_overrides.yaml`
  `missing_from_scan` and `missing_printed_pages`.

