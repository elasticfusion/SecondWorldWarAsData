# S3-event validation backstop Lambda (block b-1)

**Status:** proposed (2026-10-10). Last line of defence: validate every object written under
`output/` at the STORAGE PERIMETER, so a record that reaches S3 by ANY path (a raw-write bypass
that predates/escapes the b-2 gate, a future un-guarded writer, a partial deploy) is still caught.

## Hard requirement (owner, repeated)
The Lambda MUST run the repo's LIVE validation code — the SAME `file_lock._validate_entity` the
write path uses — NEVER a reimplementation. `update_lambdas.sh` bundles `src/` into the Lambda zip,
so the primitive is shared by construction. A deploy-time DRIFT CHECK guarantees the deployed bundle
is not behind the repo.

## Gaps identified up front (and how each is handled)
1. **Re-trigger loop.** The Lambda must never create an `ObjectCreated` event. Quarantine is
   `PutObjectTagging` (tags do NOT create a new object/version → no event) — safe. The Lambda writes
   NOTHING else. S3 notification scoped to `prefix=output/`.
2. **Key shape.** S3 keys are `output/<entity>/...` and `output/content/<book>/*-event.json`. Reuse
   the guard's own resolution (`_resolve_entity_type`, `-event.json` suffix) on the key with the
   `output/` prefix stripped — same approach as the b-3 audit. No reinvented path logic.
3. **Non-entity objects.** `output/` also holds index/`.processed_events`/metrics/parsed/report/
   `_quarantine/` objects. Reuse `_is_non_entity_file` to SKIP them (no validation, no alert).
   Already-quarantined objects (tag present) are skipped.
4. **Alert storm.** Alert ONLY on an INVALID record (never on a valid one), so a clean bulk write
   produces zero alerts. CloudWatch logs every invocation; SNS fires only on a real problem. (A
   per-run aggregation is a future nicety; alert-on-invalid-only is the v1 bound.)
5. **Two failure modes.** INVALID record → tag `quarantine=true` + ALERT (fail-LOUD). INTERNAL
   error (import/validator/transient) → log + re-raise so it surfaces in CloudWatch and the Lambda
   retry applies (bounded by maximumRetryAttempts); it must NOT silently pass.
6. **Read-only validation.** `_validate_entity` mutates its arg (PK repair/annotate) and can record
   stats — the backstop DETECTS, it does not FIX. Validate a DEEP COPY with `record_stats=False`;
   never write the record back.
7. **Quarantine enforcement is only as real as the DENY.** Tag-in-place is the MARKER; actual
   read-protection needs a bucket-policy DENY on `s3:ExistingObjectTag/quarantine=true` for CONSUMER
   roles. Today the only role is the pipeline's own producing `ECSTaskRole` — denying it would be
   self-harm. So the DENY is PARAMETERIZED (`QuarantineDenyRoleArns`, default empty = no DENY) to be
   populated when a distinct consumer/reader role (RAG/API) exists. Until then the tag is an
   advisory quarantine marker + the alert is the actionable signal. Documented, not overclaimed.

## Quarantine = TAG-IN-PLACE (never a key move)
A bad fragment holds UNIQUE cross-reference mentions; MOVING it orphans those mentions. So we tag
`quarantine=true` on the object in place (+ `quarantine_reason`), never relocate it. A lifecycle
rule / future reprocess clears the tag once the content is regenerated.

## Components
- **Handler** `lambda_handlers/s3_validation_backstop.py`: parse the event → for each key under
  `output/`: skip non-entity/quarantined → read object → `_validate_entity(deepcopy,
  record_stats=False)` → if invalid: `put_object_tagging(quarantine=true,…)` + publish to
  `{env}-wwii-phase2-complete` SNS (aggregate-safe text: key + validator keyword only, no values).
- **Delivery = EventBridge, NOT a second S3 notification rule (hard constraint).** S3 bucket
  notifications CANNOT have overlapping prefix+suffix rules for the same event type ("Configuration
  is ambiguously defined"); the stack already has an `output/content/ + -parsed.json` S3→SNS rule,
  so a `prefix=output/` notification rule would COLLIDE and break the live parse trigger. Instead:
  enable `EventBridgeConfiguration` on the bucket (added to the existing custom-resource
  `put_bucket_notification_configuration` dict, so it coexists with the current TopicConfigurations)
  and route via an **EventBridge rule** (`source=aws.s3`, `detail-type=Object Created`,
  `detail.object.key prefix=output/`) → the backstop Lambda. EventBridge rules overlap freely, so
  this does not disturb the existing S3→SNS notifications at all.
- **CFN**: the Lambda (code from the shared `lambda/code.zip`, env `VALIDATION_FINGERPRINT`/
  `ENV_NAME`/`S3_BUCKET`/`NOTIFICATION_TOPIC_ARN`), the EventBridge rule + `AWS::Lambda::Permission`,
  least-privilege IAM (`s3:GetObject`+`s3:GetObjectTagging`+`s3:PutObjectTagging` on `output/*`,
  `sns:Publish` on the one topic, logs), a DLQ for the async invoke, and the PARAMETERIZED
  quarantine-DENY bucket-policy statement (`QuarantineDenyRoleArns`, default empty = no DENY).
- **Drift check** in `scripts/check_component_versions.sh`: repo-HEAD fingerprint
  (`scripts/validation_fingerprint.sh` over `src/schemas/` + `file_lock.py` + `validation_stats.py`)
  vs the deployed Lambda's `VALIDATION_FINGERPRINT` env (stamped by `update_lambdas.sh`). WARN
  (or `--strict` fail) on mismatch so a partial deploy can't leave the backstop behind the pipeline.
- **Tests** for the handler (invalid→tag+alert; valid→noop; non-entity→skip; already-quarantined→
  skip; read-only) + the fingerprint helper determinism.

## Rollout / cost
Code-complete + merged; NOT auto-deployed (deploy is a separate deliberate step). The Lambda is
event-driven (no idle cost). No AWS resources left running by this work.
