# Security Posture

Tracked record of the pipeline's security posture: what is solid, what gaps were
found + fixed, and what is deliberately deferred to the API/UI phase. Last
reviewed 2026-10-01 (after the AV-scanning + S3/pandoc hardening pass).

Companion: `AV_SCANNING_DESIGN.md` (malicious-document scanning),
`INGESTION_PATHS.md` (per-type routing incl. the AV gate).

---

## Solid (verified in-repo)

- **S3 data bucket**
  - Public access fully blocked (`BlockPublicAcls/BlockPublicPolicy/IgnorePublicAcls/RestrictPublicBuckets`).
  - **Encryption at rest** declared: SSE-S3 (AES256), `BucketKeyEnabled` (PR #192).
  - **TLS-only**: `DataBucketPolicy` denies `aws:SecureTransport=false` (PR #192).
  - Versioning enabled; lifecycle expiry on tmp/cache/old versions.
  - `DeletionPolicy: Retain` on bucket + all DynamoDB tables (no accidental data loss on stack delete).
- **IAM** — scoped to env-prefixed ARNs (`${EnvironmentName}-wwii-*`). The few
  `Resource: "*"` entries are actions AWS *requires* to be `*` (`ecs:ListTasks`,
  `batch:ListJobs`, CloudWatch `PutMetricData`) and are annotated as such.
- **Network** — SGs are egress-only to `0.0.0.0/0` with SG-to-SG ingress on
  443/7001 (OpenSERP). No open inbound from the internet; tasks run in private
  subnets with `AssignPublicIp: DISABLED`; NAT is torn down when idle.
- **Secrets** — Grok API key in Secrets Manager (`SECRETS_ID`), read at runtime;
  not baked into images. `config.yaml` (secrets) is git-ignored.
- **Compute storage encryption at rest**
  - **Fargate tasks** (phase0/1/2/3, convert, video, AV scan) — ephemeral task
    storage is **auto-encrypted by AWS** (AES-256, platform ≥ 1.4.0); not
    configurable, always on.
  - **GPU OCR Batch instances** (Chandra) — launch-template EBS volume now
    `Encrypted: true` (ocr.yaml), AWS-managed EBS key. These EC2-backed instances
    write raw source docs + OCR output to local disk during processing.
  - **Account-wide** EBS encryption-by-default enabled (region us-east-1) as
    defense-in-depth so any future volume is encrypted even if a template omits it.
- **Ingestion front door (defense-in-depth)**
  - **AV scanning** of all uploaded binaries before any parser touches them
    (demand-launched, binary-only ClamAV; infected → quarantine + freeze hook;
    fail-closed) — `AV_SCANNING_DESIGN.md`.
  - **Media-type mismatch** rejection (declared ext ≠ sniffed bytes) blocks
    disguised payloads even when AV is disabled.
  - **Reject-the-failures net** — no silent drops; every failed/unusable route
    off-ramps to `needs-review/` + alert.
- **Outbound enrichment fetches** — every endpoint is a hardcoded `https://`
  literal to a known host (Wikipedia, Commons, NARA, open-elevation, Nominatim,
  NOAA); entity text goes through `requests` `params=` (auto URL-encoded). Hosts
  are **not** entity-derived, so these are not an SSRF vector. OpenSERP URL is
  operator-config, internal.
- **pandoc convert** — runs with **`--sandbox`** (PR #193): a crafted upload
  cannot read local files or make network requests during conversion (mitigates
  the pandoc SSRF/local-file-read CVE class + IMDS egress on the convert path).
  Invoked as an argv list (no shell), with timeout + explicit error handling.

## Gaps found + fixed in this pass

| Gap | Fix | Ref |
|---|---|---|
| S3 encryption-at-rest not declared (relied on default) | `BucketEncryption` SSE-S3 AES256 + BucketKey | PR #192 |
| No TLS-only enforcement on the bucket | `DenyInsecureTransport` bucket policy | PR #192 |
| Uploaded binaries parsed with no malware scan | demand-launched binary-only ClamAV AV gate | PR #191 |
| pandoc ran without `--sandbox` on attacker-controlled docs | add `--sandbox` | PR #193 |

## Deferred to the API/UI phase (design-now, enforce-later)

These are **not** current vulnerabilities — the pipeline today ingests via S3
events from a trusted operator, with no public submission endpoint. They become
required the moment an API/UI accepts third-party uploads:

- **AuthN/AuthZ** — the submission endpoint needs real auth. Per
  `architecture-decisions.md`, API Gateway usage plans (free quota + throttling)
  are the planned mechanism; metering is cost-protection first.
- **Freeze-submitter enforcement** — the hook exists (`av_scan.freeze_submitter`)
  but is a no-op today because S3-event uploads carry no principal. Requires the
  submission path to stamp submitter identity (`x-amz-meta-submitter` /
  `x-amz-meta-api-key-id`), then flip the hook to disable the API-key / set
  `account.frozen`. Identity-provenance requirement documented in `AV_SCANNING_DESIGN.md`.
- **Upload abuse controls** — per-caller rate limits, size caps, quotas (API
  Gateway usage plans double as the security control).
- **Zip-bomb / decompression limits** at the archive-expansion pre-stage
  (resource-exhaustion DoS). Bound expansion ratio + total output size.
- **PII / data governance** — accepting third-party uploads introduces a
  data-governance surface the current public-domain corpus does not have.

## Review cadence

Re-review this document when: (a) a public submission path (API/UI) is added;
(b) a new external fetch or parser is introduced; (c) IAM/network topology
changes materially.
