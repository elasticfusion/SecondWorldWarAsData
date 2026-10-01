# Malicious-Document / AV Scanning — Design

Status: **design** (2026-10-01). Not yet built. Scopes antivirus / malicious-input
scanning at the ingestion front door, as part of ingestion management
(companion: `INGESTION_PATHS.md`).

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

## Where to scan

At the **front door, before `_split_by_media`** hands bytes to any track. One
scan protects OCR / convert / video uniformly.

```
upload → _content_keys → [AV SCAN] → clean?  → _split_by_media → tracks
                                   └ infected → QUARANTINE + alert (never processed)
```

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

## Recommendation

Two viable architectures:
- **(A) Hybrid:** ClamAV-on-Lambda for small files (fast door-side reject), Fargate
  scan for video. Best latency/cost for the common case; more moving parts.
- **(B) Uniform Fargate scan:** one ClamAV scan task for all uploads. Sidesteps the
  size cliff, reuses the ECS/launch machinery we already run for every track;
  simpler to operate; costs a task spin-up per upload.

**Lean: (B)** for a first cut — uniform, no size cliff, matches existing pattern —
then optimize to (A) if door-side reject latency/cost matters. Either way the
quarantine seam + `freshclam` signature-update job are shared.

## Build checklist (when scheduled)

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
