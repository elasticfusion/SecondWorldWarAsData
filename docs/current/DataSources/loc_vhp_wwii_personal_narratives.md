# Library of Congress — VHP WWII Personal Narratives

Extracts WWII oral histories and related artifacts from the Library of Congress [Veterans History Project](https://www.loc.gov/) search results.

## Source

| Field | Value |
|-------|-------|
| Organization | Library of Congress |
| Collection | Veterans History Project (American Folklife Center) |
| Search URL | [WWII personal narratives (150 per page)](https://www.loc.gov/search/?fa=original-format%3Apersonal+narrative%7Csubject%3Aworld+war%2C+1939-1945&sb=date&st=list&c=150) |
| API | LOC JSON API (`?fo=json` on search and item URLs) |
| Script | `scripts/extract_loc_vhp_wwii_personal_narratives.py` |

The extractor uses the LOC JSON API rather than HTML scraping. Requests use a Chrome user-agent, configurable delays, retries with backoff, and on-disk caching under `contentrepository/LibraryOfCongress/VHP/`.

## Command line

### Test run

Quick sanity check — first search page only (~150 narratives). Uses cache when available.

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py --max-pages 1
```

Optional slower pacing for testing:

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py --max-pages 1 --delay-sec 1.0 --jitter-sec 0.25
```

### Full production run

Recommended for a complete extract — all search results (typically 1,000+ individuals), linked artifacts, video URLs, transcript flags, polite default pacing:

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py --fresh
```

Use `--fresh` when replacing an earlier truncated extract or when you want a new `captured_at` timestamp. Omit `--fresh` only to resume an interrupted run.

Explicit defaults (equivalent pacing to above without `--fresh`):

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py --delay-sec 2.0 --jitter-sec 0.75
```

Gentler pacing if LOC appears slow or throttling:

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py --delay-sec 3.0 --jitter-sec 1.5
```

### Switches reference

| Switch | Default | Purpose |
|--------|---------|---------|
| `--max-pages` | all pages | Limit search pagination — **test only** |
| `--delay-sec` | `2.0` | Minimum seconds between HTTP requests |
| `--jitter-sec` | `0.75` | Random extra delay added to each request |
| `--expand-segments` | off | Per-page image URLs for multi-page scans (albums, diaries) — not needed for oral-history video focus |
| `--fresh` | off | Ignore checkpoint and start a new run |
| `--resume` | auto if checkpoint exists | Resume an interrupted run |
| `--resume-overlap` | `5` | Re-process this many individuals before the failure point (guards against truncated cache) |
| `--csv-checkpoint-every` | `1500` | Rewrite CSV outputs every N individuals during processing (`0` disables) |

### When to use `--expand-segments`

Use only when you need **per-page** scanned image URLs and titles for multi-page artifacts (photo albums, diaries, letter stacks).

| Material | Default run | With `--expand-segments` |
|----------|-------------|--------------------------|
| Video/audio interviews (MP4, MP3) | Full URLs captured | No change |
| Single-file photos, citations | Full URLs captured | No change |
| Multi-page albums/diaries | Cover thumbnail + viewer URL | One row per page with full JPG URL and page title |

Default run is sufficient for veteran metadata and oral-history video. Segment expansion adds ~1,000+ extra API calls.

## Outputs

| File | Rows (typical) | Contents |
|------|----------------|----------|
| `contentrepository/indexes/loc_vhp_wwii_personal_narratives_individuals.csv` | 1,000+ | One row per veteran — identity and service metadata |
| `contentrepository/indexes/loc_vhp_wwii_personal_narratives_artifacts.csv` | varies | One row per artifact (video, audio, photo, document, etc.) |
| `contentrepository/indexes/loc_vhp_wwii_personal_narratives_manifest.json` | — | Run summary, linking schema, artifact type counts |
| `contentrepository/LibraryOfCongress/VHP/search_pages/` | — | Cached search JSON (per page) |
| `contentrepository/LibraryOfCongress/VHP/segment_searches/` | — | Cached segment JSON (only when `--expand-segments` is used) |

Every row includes `captured_at` (ISO 8601 UTC, e.g. `2026-06-28T12:14:56Z`) set once at the start of each extraction run.

During processing, the script rewrites both CSVs and the manifest every **1,500** individuals (default). Partial outputs set `manifest.status` to `partial` and include `total_expected_individuals`. A final successful run sets `status` to `complete`. On failure, the script also writes whatever individuals were checkpointed so far.

## Linking individuals and artifacts

The dataset is split into two normalized CSVs with explicit cross-references (no SQLite).

### Individuals → artifacts

| Column | Meaning |
|--------|---------|
| `metadata_page_url` | Primary key — LOC `/item/` page for this veteran |
| `artifact_index_file` | Path to the artifacts CSV |
| `artifact_ids` | Pipe-delimited (` \| `) list of `artifact_id` values |
| `artifact_row_count` | Number of rows in the artifacts file for this person |
| `artifact_count` | Distinct artifacts (sequences) for this person |

### Artifacts → individuals

| Column | Meaning |
|--------|---------|
| `artifact_id` | Primary key — `{collection_number}:{sequence}` or `{collection_number}:{sequence}:{segment}` |
| `individual_index_file` | Path to the individuals CSV |
| `individual_metadata_page_url` | Foreign key matching `metadata_page_url` on the individual row |

### Join examples

```text
artifacts.individual_metadata_page_url = individuals.metadata_page_url
individuals.artifact_ids contains artifacts.artifact_id
```

## Source URLs

| Level | Column | What it is |
|-------|--------|------------|
| Search | `source_search_url` | LOC search results page (both files) |
| Individual metadata | `metadata_page_url` | LOC `/item/` page — biography, service history, collection notes |
| Collection | `collection_mets_url` | METS metadata files (individuals only) |
| Artifact viewer | `artifact_resource_page_url` | LOC `/resource/` page for that artifact |
| Media files | `artifact_file_url_mp4`, `_mp3`, `_pdf`, `_image`, `_stream`, `_primary`, `_all_file_urls` | Direct download or stream URLs |

## Transcript columns

Transcripts are not always exposed as downloadable LOC resources. The extractor records both **mentions** and **linked files**.

### Individuals CSV

| Column | Meaning |
|--------|---------|
| `transcript_mentioned_in_materials` | `true` if LOC `materials` notes mention a transcript |
| `transcript_artifact_ids` | Pipe-delimited IDs of transcript-type artifact rows |

### Artifacts CSV (video, audio, transcript rows)

| Column | Meaning |
|--------|---------|
| `transcript_available` | `true` when a downloadable transcript file is linked |
| `transcript_artifact_id` | ID of the transcript artifact (on video/audio: linked transcript; on transcript rows: self) |
| `transcript_file_url` | Direct URL — PDF, XML, or scanned image |

**Detection rules:**

- Transcript artifacts: `transcription` or `interview` type, or caption/label contains "transcript"
- Video/audio rows: `transcript_available=true` only when the same collection has a transcript artifact with a file URL
- Materials mention (`transcript_mentioned_in_materials=true`) does **not** set `transcript_available` on video if no file is exposed in the API

**Example:** Nathan E. Cook has `transcript_mentioned_in_materials=true` but video `transcript_available=false` — LOC notes a transcript in collection materials but does not expose it as a separate downloadable resource in the JSON.

## Artifact types

Counts vary with the number of individuals extracted. A prior 329-individual run produced roughly: video 237, audio 125, personal 1,394, other 86, official 81, photos 29, memoir 19, diary 13, artwork 10, transcription 4, interview 2, map 1. A full 1,000+ individual run will be larger. See `loc_vhp_wwii_personal_narratives_manifest.json` after completion for exact counts.

## Individuals CSV — key metadata columns

Identity and service: `veteran_name`, `birth_place`, `gender`, `rank`, `branch_of_service`, `unit_of_service`, `service_location`, `war_or_conflicts`, `battles_campaigns`, `dates_of_service`, `entrance_into_service`, `service_history_json`, `description`, `interview_collection_notes`, contributors, subjects, formats.

## Artifacts CSV — key metadata columns

`artifact_sequence_in_collection`, `artifact_resource_type`, `artifact_caption`, `artifact_resource_label`, `artifact_duration_seconds`, `artifact_segment_count`, media URL columns, `raw_resource_json` (full LOC resource object).

## Pagination note

The LOC search API reports `pagination.total: 329` for this query, but that value is stale — each page still returns 150 new veterans and the UI shows ranges like `1201 - 1350` on page 9. The script **does not** stop at the reported total. Pagination continues until one of these conditions:

- empty page
- short page (fewer than `perpage` results)
- no `next` link
- a full page with zero new unique IDs (guards against repeats)

The API also exposes `pagination.of` (e.g. 49,285), which is the broader LOC online catalog size, not the filtered WWII personal-narrative count.

## Rate limiting and CAPTCHA

The browser search UI can show a CAPTCHA during heavy browsing. This extractor uses the JSON API (`?fo=json`), not the HTML UI, and has not encountered CAPTCHA challenges on API responses. Transient upstream errors (HTTP 429, 5xx, 520, 522, 524, truncated JSON) are retried up to 8 times with exponential backoff (extra delay for 520-class errors); HTTP 403 fails immediately. Search results are read page-by-page from disk during processing to avoid loading tens of thousands of records into memory. On resume, pagination continues from the last cached search page instead of replaying from page 1. Default pacing is 2.0s + up to 0.75s jitter between requests. If a run fails mid-way, re-run the same command to resume (do not use `--fresh`). Increase `--delay-sec` and `--jitter-sec` if failures persist.

## Checkpoint and resume

The script saves progress after each search page and each individual processed.

| File | Purpose |
|------|---------|
| `contentrepository/LibraryOfCongress/VHP/checkpoint/state.json` | Run status, phase, last completed index, `captured_at` |
| `contentrepository/LibraryOfCongress/VHP/checkpoint/individuals.jsonl` | One individual row per completed record |
| `contentrepository/LibraryOfCongress/VHP/checkpoint/artifacts.jsonl` | Artifact rows appended as each individual completes |

### If the script fails

Re-run the same command — it resumes automatically when a checkpoint exists:

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py
```

On resume the script:

1. Re-downloads search pages from the last attempted page onward (invalidates possibly truncated cache).
2. Drops the last `--resume-overlap` individuals (default **5**) from the checkpoint and re-processes them.
3. Invalidates segment caches for those overlap individuals when `--expand-segments` was used.
4. Continues through remaining individuals, then writes CSVs when complete.

### Start over

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py --fresh
```

A successful run marks the checkpoint `completed`. The next run without `--fresh` starts a new extraction (search cache is still reused).

## Re-running

Cached search pages under `contentrepository/LibraryOfCongress/VHP/search_pages/` are reused on subsequent runs, so re-extraction is fast and does not re-hit LOC unless cache is cleared or pagination changes.

To refresh with a new capture timestamp:

```bash
python3 scripts/extract_loc_vhp_wwii_personal_narratives.py --fresh
```