# Malicious-Document / AV Scanning — Design

Status: **BUILT and deployed** (designed 2026-10-01; reviewed against the code 2026-11-16). Scopes antivirus / malicious-input
scanning at the ingestion front door, as part of ingestion management
(companion: `INGESTION_PATHS.md`).

---

## Submitter accountability: freeze-on-infection (requirement + hook)

An infected upload is not just a bad file — it is a signal about the **submitter**.
Requirement: **a file found infected freezes the submitting user / API-key owner
pending human review** (block further submissions from that principal until an
operator clears them).

**Today there is NO submitter identity.** Uploads arrive via **S3 events** with no
principal attached; the only "API key" in the system is *our outbound Grok key*,
not a submitter credential. So freeze cannot be enforced yet. Therefore:

- **Now (this build):** implement the freeze as a **hook** — on infection, call a
  `freeze_submitter(identity)` seam that records the intent (structured log +
  alert + a `quarantine/frozen/{identity}.json` marker) and is a **no-op when no
  identity is present** (the S3-event case). Fail-safe: never blocks ingestion
  machinery, never raises.
- **Identity-provenance requirement (capture now so freeze is wireable later):**
  when the API/UI submission path is built, it MUST stamp submitter identity onto
  the upload — S3 object metadata `x-amz-meta-submitter` / `x-amz-meta-api-key-id`
  (and/or a DynamoDB upload record). The AV seam already reads this metadata; the
  day identity exists, `freeze_submitter` flips from record-intent to
  enforce (disable the API key / set an account `frozen` flag) with no redesign.
- **Enforcement point (future):** API Gateway usage-plan key disable + an
  `account.frozen` check at the submission Lambda; until then the recorded
  freeze-intent markers are the audit trail an operator acts on manually.

---

## Why (threat model)

Every uploaded file is attacker-controllable and is handed to a binary parser:
pandoc (epub/docx), **PyMuPDF/`fitz`** (PDF text + image rendering), **ffmpeg**
(video), **Chandra/torch** (OCR on images). Each is non-trivial native/parsing
attack surface with a CVE history. The threat is NOT only "a document carries a
macro" — it is "a crafted file exploits the parser that opens it," which can
happen on a file that otherwise converts/OCRs/transcribes **successfully**.

### Does pandoc strip macros? (common assumption — analyzed)

**Mostly yes, but incidentally and narrowly — do not treat it as the defense.**
- A `.docx` is a ZIP of XML parts; VBA macros live in a separate binary part
  (`word/vbaProject.bin`), outside the document text. Pandoc reads only the
  content parts and emits plain markdown, so macros / OLE objects / embedded
  executables simply don't survive into the output. The classic Office-macro
  vector is effectively neutralized **on the convert track**.
- Caveats that mean it is NOT our AV:
  1. **Incidental, not a security contract** — pandoc converts, it does not
     sanitize; behavior can change by format/version.
  2. **Pandoc itself is attack surface** — crafted input has yielded pandoc CVEs
     (e.g. SSRF / local-file disclosure); the *parse* can be the exploit, before
     any markdown exists.
  3. **Convert track only** — it does nothing for the PDF (fitz), video (ffmpeg),
     or image (torch) parsers, which are the higher-value targets.

Conclusion: scanning must happen **before any parser runs**, for **all** tracks —
not gated on conversion-failure (a cleanly-converting file can still be the
payload).

## Scope: which file types actually need scanning

AV is **not uniform across types** — scan the binaries whose parsers are attack
surface; skip inert text.

| Type | Carrier risk in OUR pipeline | Scan? |
|---|---|---|
| `.txt`, `.md` | **None.** Inert data; we parse it / send it to Grok as text — never render in a browser or execute it. (Counterfactual where it *would* matter: markdown rendered to HTML in a browser that runs embedded `<script>`, or a byte-polyglot fed to another interpreter — neither occurs here. A giant text "bomb" is a DoS/resource concern, bounded by size/near-empty checks, not malware.) | **No** |
| `.html` | **Low.** `<script>`/handlers/data-URIs are dangerous only when rendered in a browser or fetched by a tool; we send HTML to pandoc/parser, not a browser, so active content is inert. Residual risk is pandoc/parser-side (crafted HTML → SSRF/file-read in the parser), not "infection." | Optional / parser-hardening, not AV |
| `.pdf` | **Yes** — PyMuPDF/`fitz` renders pages/images; historic malware vector. | **Yes** |
| images (`.jpg/.png/.tif/...`) | **Yes** — decoded by torch/Chandra; crafted-image parser exploits. | **Yes** |
| video (`.mp4/.mkv/...`) | **Yes** — ffmpeg; many CVEs. | **Yes** |
| Office (`.docx/.xlsx/.pptx/...`) | **Yes** at the door, though conversion-to-text neutralizes macros (below). | **Yes** |

Conclusion: **scan binaries (PDF/image/video/Office); skip `.txt`/`.md`; HTML is
parser-hardening, not AV.** This also shrinks AV cost/latency — the common
small-text uploads bypass the scanner entirely.

## Office formats: convert-to-safe-text is the primary neutralization

Same mechanic as docx macros (above): extracting to text/CSV/images drops the
executable payload. These are **convert-track** additions (not yet built):

- **Excel `.xlsx`/`.xlsm` → CSV** (openpyxl/pandas): extracts cell **values**,
  dropping VBA (`xl/vbaProject.bin`), OLE objects, DDE. Feeds the **structured/OOB
  track** (tabular). Note: a cell literally containing `=cmd|...` survives *as
  text* into CSV — harmless here (we never open the CSV in Excel) but sanitize a
  leading `= + - @` if a CSV is ever re-exported for a human.
- **PowerPoint `.pptx` → text + images** (python-pptx): slide text → narrative
  markdown; slide images → the **vision captioner**. Macros/OLE dropped by
  extraction.

AV remains the backstop for the *parse step itself* and for any Office file that
fails/short-circuits conversion.

## Where to scan — demand-launched, binary-only

AV runs **only when a binary is in the upload batch**, and the scanner is
**demand-launched** (never a standing service — same pattern as OCR/convert/video,
which launch per-upload). Text-only batches (`.txt`/`.md`/`.html`) **skip AV
entirely** and never spin the container → zero AV cost for the common case.

```
upload → _content_keys → _split_by_media
   ├─ any binary key present (pdf/image/video/office)?
   │     → demand-launch ClamAV scan task (Fargate) over the binary keys
   │         ├─ clean    → proceed to OCR / convert / video
   │         └─ infected → QUARANTINE + alert (never processed)
   └─ text/md/html only → straight to parse (no AV)
```

Gate: launch the scan iff `ocr_keys ∪ convert_keys ∪ video_keys` is non-empty.
Scanning happens **before** those tracks' processors (fitz/ffmpeg/torch/pandoc)
touch the bytes.

## Infected handling: QUARANTINE, not delete

Move the file to a `quarantine/` prefix (outside `contentrepository/`, so it never
re-triggers), reject-to-review + alert (reuse `review_reject`), and let a human
decide. Rationale:
- **Preserves evidence/provenance** (matches the project ethos — even failed
  videos are retained).
- **Survives false positives** (AV engines do flag benign files).
- Hard auto-delete is irreversible and destroys the audit trail. (A later retention
  policy can purge confirmed-malicious quarantine items on a timer.)

## Engine: ClamAV — can it run on Lambda?

**Yes, with constraints** (AWS has a published ClamAV-on-Lambda reference), but
our file sizes force a placement decision.

- **Signature DB (~1 GB+, daily-updated)** can't be bundled fresh. Standard
  pattern: a **scheduled `freshclam` Lambda** writes signatures to **S3**; the
  scan function **syncs S3 → `/tmp`** (or mounts **EFS**) at cold start.
- **Cold start + memory**: loading the DB needs seconds + ~1–2 GB RAM; container-
  image Lambda (bundle ClamAV, up to 10 GB) is the cleanest packaging.
- **File-size cliff (the deciding factor)**: Lambda `/tmp`/memory bound the
  scannable size. Our corpus has **40–90 MB PDFs** (fine) but **~2.4 GB videos**
  (do NOT fit a modest Lambda `/tmp`). Large video needs **EFS-on-Lambda** or a
  **Fargate** scan.

| Placement | Fit |
|---|---|
| ClamAV container-image Lambda + S3 signatures | ✅ docs/images/epub/docx (clean, cheap, fast-reject before any task launch) |
| …same Lambda for 2.4 GB video | ⚠️ needs EFS mount or won't fit |
| **ClamAV in a Fargate task** (reuse our ECS pattern) | ✅ any size, no `/tmp` ceiling; cost = a task launch per scan |

## Recommendation (decided)

**Demand-launched, binary-only ClamAV scan on Fargate.** Settled per operator
decision 2026-10-01:
- Launch the scan task **only when the upload batch contains a binary** (never a
  standing service; text/md/html skip AV) — matches the OCR/convert/video
  demand-launch pattern.
- **Fargate** (not Lambda) so any size scans uniformly — avoids the ~2.4 GB video
  `/tmp`/memory cliff and reuses the ECS launch machinery we already run.
- A hybrid (Lambda for small files, Fargate for video) remains a later latency/cost
  optimization if needed, but adds moving parts; start uniform-Fargate.
- Shared regardless: the `quarantine/` seam + the `freshclam` signature-update job.


## As built (2026-11-16)

- **Gate:** `trigger_handler._av_gate` -> `_av_scan_and_filter` -> `src/ingestion/av_scan.scan_and_gate`.
  Binary keys = the OCR, convert and video lists (`select_binary_keys`, suffix list in
  `av_scan._BINARY_SUFFIXES`). Clean keys proceed; infected or unscannable keys are quarantined and
  alerted; any gate exception fails closed (binaries held in S3, nothing advances).
- **Engine:** ClamAV in its own container (`Dockerfile.clamav`, `scripts/av_entrypoint.py`), run as a
  demand-launched Fargate task (`{env}-wwii-av-scan`, `cloudformation/compute.yaml`) via
  `av_scan.EcsClamavScanner`, which passes `AV_KEYS` and reads the verdict JSON from
  `av-scan/results/<run>.json`. A key missing from the verdict JSON, or missing/empty signatures, is
  `ERROR` (fail-closed). Signatures come from `s3://<bucket>/clamav-sigs/`, refreshed by the
  `{env}-wwii-av-freshclam` task on a `rate(1 day)` EventBridge rule.
- **Startup guard:** `trigger_handler._enforce_av_gate_policy` raises at import if `AV_SCAN_ENABLED`
  resolves false outside dev/local/test (`OBSERV av.gate_enabled` / `av.gate_disabled`).
- **Quarantine:** `av_scan._quarantine` moves the key to `quarantine/<key>` and rejects to review;
  `freeze_submitter` writes `quarantine/frozen/<identity>.json` when a submitter identity exists
  (still none for S3-event uploads) and is otherwise a no-op.
- **Second entry point (not in the original design):** map/image binaries downloaded during Phase 3
  are staged under `unscanned/` (`ecs_entrypoint.py`, Phase-3 final sync) and scanned by
  `av_scan.drain_unscanned` when Phase 3 completes (`trigger_handler._drive_next_phase`, `OBSERV
  av.phase3_drain`): clean -> production path, infected -> `quarantine/`, unscannable -> stays in
  `unscanned/` and is retried. A single-drainer lock (`_acquire_drain_lock`) serialises drains.
- **Not built:** Excel/PowerPoint to text converters (the Office section above). `.xlsx`/`.pptx`
  are scanned as binaries but `src/ingestion` has no converter for them.

## Build checklist (original; status 2026-11-16)

Built: ClamAV container (Fargate), `freshclam` job, pluggable `Scanner` seam, `quarantine/` +
reject-to-review + alert, fail-closed outage handling, tests. The Lambda-placement and size-routing
items were superseded by the uniform-Fargate decision above. Original list:


- [ ] ClamAV packaging (container image: Lambda or Fargate per decision).
- [ ] `freshclam` signature-update job (scheduled) → S3; scanner syncs/mounts.
- [ ] Scan seam at the door (before `_split_by_media`); pluggable so the engine
      can be swapped/mocked (tests must not need a real engine).
- [ ] `quarantine/` disposition + `reject_to_review(category="av-infected")` + alert.
- [ ] Size routing: large video → size-capable scanner.
- [ ] Fail-closed vs fail-open decision for a scanner *outage* (recommend
      fail-closed → quarantine-for-review, so an unscanned file never auto-advances).
- [ ] Tests: clean passes, infected quarantined + alerted, scanner-down path.

## Interim mitigation already in place

The **media-type mismatch** check (`detect_media_mismatch`) rejects a file whose
bytes contradict its extension (a `.pdf` that is really an executable/script/zip)
to needs-review before processing. This is a *type* check, not malware detection,
but it already blocks a class of disguised-payload uploads today.
