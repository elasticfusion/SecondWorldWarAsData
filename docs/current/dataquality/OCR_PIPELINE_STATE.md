# OCR / GPU Pipeline — Current State

_Branch: `feature/concurrency-parallelism` · Last updated: 2026-09-29_

Status of the unattended, document-parallel WWII pipeline with a focus on the
GPU-OCR front door and the networking-lifecycle hardening that makes it reliable.

---

## Where we are

The OCR RUNNABLE-stall and the follow-on container-pull failure are **both
fixed and deployed**. A live on-demand OCR job (`B406`, 11 pages) has:

1. Passed the **readiness gate** (NAT + all 7 VPC interface endpoints
   `available`) before compute was requested.
2. **Registered** its GPU instance with the ECS/Batch cluster (the thing that
   failed every prior attempt).
3. **Pulled the Chandra image** via the ECR endpoints (past the point that
   failed with `CannotPullECRContainerError`).
4. Loaded the model and is **OCR-ing pages 1..11** under the progress watchdog.

Remaining to fully close: let the job finish → `ocr-output/B406/…` →
EventBridge → merge Lambda writes the chapter structure → parse triggers; then
tear down.

### Two root causes fixed this session

| # | Symptom | Root cause | Fix (commit) |
|---|---------|-----------|--------------|
| 1 | GPU job stuck RUNNABLE; instance launches but never registers | NAT + interface endpoints not reliably up at boot; nat_manager **create-after-delete race** (a `deleting` endpoint counted as "present" → recreation skipped) | Readiness contract `_verify_ready` + exclude `deleting` from the exists-check + `_wait_out_deleting` (`4bb5ee0`) |
| 2 | Instance registers, job RUNNING, then `CannotPullECRContainerError` (ECR auth timeout) | A **direct `action=delete`** tore down NAT+endpoints **under a running job** — the demand guard only ran on the SNS path | Move demand guard **into `_delete_all`** (every delete path honors in-flight OCR); `force=True` for intentional teardown (`561e71f`) |

### Not a bug (clarified)

- **Spot not placing immediately is expected.** Spot waits for capacity; the
  Option-B controller falls back to on-demand after the threshold (48h cap).
  On-demand places immediately (768 quota). Only the spot *quota* matters, and
  the ≥80% warning already surfaces it.

---

## Flow + decision points

```mermaid
flowchart TD
    A[PDF uploaded to S3 raw] --> B[trigger_handler: route by type]
    B -->|PDF/image| C{OCR intake claim<br/>ocr#book exists?}
    C -->|claimed already| C1[Deny duplicate submit]
    C -->|new| D[Best-guess chunking<br/>_ocr_chunks]

    D --> E[_ensure_nat_for_ocr:<br/>invoke nat-manager action=create]

    E --> F{{"READINESS GATE<br/>_verify_ready"}}
    F -->|not_ready<br/>missing endpoints/NAT| E
    F -->|ready: NAT + all 7<br/>endpoints available| G[Submit Batch OCR job]

    G --> H{Queue}
    H -->|spot| I[dev-wwii-chandra-gpu<br/>waits for capacity]
    H -->|on-demand| J[dev-wwii-chandra-gpu-ondemand<br/>places immediately]
    I -->|no capacity<br/>&gt; threshold| K[ocr_spot_controller:<br/>route to on-demand<br/>48h cap]
    K --> J

    I --> L[GPU instance launches]
    J --> L
    L --> M{{"Registers with ECS?<br/>needs egress:<br/>ecr.api/dkr, ecs,<br/>ecs-agent, ecs-telemetry,<br/>logs, secretsmanager"}}
    M -->|endpoints present| N[STARTING: pull Chandra image via ECR endpoint]
    M -->|no egress| M1["FAIL: CannotPullECRContainerError<br/>(fixed: readiness gate + teardown guard)"]

    N --> O[RUNNING: download PDF via S3 gateway endpoint]
    O --> P[Chandra OCR pages 1..N<br/>progress watchdog 900s]
    P --> Q[Write ocr-output/book/...]

    Q --> R[Batch job SUCCEEDED]
    R --> S[EventBridge: dev-wwii-ocr-succeeded]
    S --> T[ocr_merge_handler]
    T --> U["Write contentrepository/book/chapter1/<br/>chapter1-content.md + chapter1-meta.yaml (A1)"]
    U --> V[content-upload event triggers parse]

    V --> W[Phase 1 parse → JSON]
    W --> X[Phase 2 extract entities Grok Batch]
    X --> Y[Dedup at intake / front door]
    Y --> Z[Phase 3 enrich]
    Z --> ZZ[done]

    %% teardown decision
    R --> TD{{"nat-manager delete<br/>_nat_demand_present?"}}
    TD -->|OCR jobs in flight| TD1[REFUSE teardown<br/>keep NAT+endpoints up]
    TD -->|idle & not force| TD2[Tear down NAT + endpoints]
    TD -->|force=true operator| TD2
```

### Key decision points (numbered)

1. **OCR intake claim** — `ocr#{book}` DynamoDB claim guards the whole chunk
   set; duplicate submits denied at the front door (dedup principle).
2. **Chunking** — page-count > 50 → 50-page chunks; image → single; unknown →
   safe whole fallback (`_ocr_chunks`).
3. **Readiness gate** (`_verify_ready`) — compute is **not requested** until
   NAT is `available` AND every required interface endpoint is `available`.
   Returns `{ready, missing[]}`. This is what makes registration reliable.
4. **Queue / spot-vs-on-demand** — spot waits for capacity (expected);
   `ocr_spot_controller` falls back to on-demand after the threshold (48h cap).
5. **Registration egress** — the instance needs the 7 interface endpoints
   (ECR/ECS/logs/secrets) to register + pull; S3 uses the gateway endpoint.
6. **Teardown guard** (`_delete_all` + `_nat_demand_present`) — refuses to tear
   down NAT/endpoints while any OCR job is non-terminal on either queue, unless
   `force=true`. Prevents infra being pulled out from under a running job.

---

## Verification evidence (this session)

- `action=verify` → `{"ready": true, "missing": []}` with all 7 endpoints
  available.
- OCR job `3283f45d-…`: RUNNABLE → STARTING (`registered=1`) → RUNNING;
  container log shows PDF downloaded, `Model loaded successfully`,
  `Loaded 11 page(s)`, `Processing pages 1-1...`.
- Gate: `scripts/gate.sh` PASS, 1161 tests (commits `4bb5ee0`, `561e71f`).

## Follow-ups

- EIP release `AuthFailure` on teardown (IAM perm gap) — minor, cleanup only.
- Full-chain close (#5): confirm merge → chapter structure → parse after this
  job SUCCEEDED; then a **true** front-door test (upload via S3 so the trigger
  does chunking + NAT-ensure + claim end-to-end).
- Tear down GPU/NAT after testing.
