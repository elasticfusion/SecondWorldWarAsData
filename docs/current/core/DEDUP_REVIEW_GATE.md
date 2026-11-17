Status: implemented (`ecs_entrypoint.py`, `src/dedup/`, `lambda_handlers/dedup_ui_handler.py`, `lambda_handlers/trigger_handler.py`, `cloudformation/compute.yaml`, `cloudformation/events.yaml`).

# Dedup review gate (Phase 2 to Phase 3)

This is the whole duplicate-review step: detection, auto-merge, the gate decision, the notification, the review UI, and how Phase 3 starts afterwards. Matching rules live in other docs and are not repeated here:

- People matching: [deduplication.md](../features/people/deduplication.md), [dedup-weighting.md](../features/people/dedup-weighting.md)
- Group (unit) matching: [group-dedup-unit-key.md](../features/people_groups/group-dedup-unit-key.md), [GROUP_DEDUP_RULES_REVIEW.md](../features/people_groups/GROUP_DEDUP_RULES_REVIEW.md)
- Deployment and operations: [AWS_DEPLOYMENT.md](../AWS_DEPLOYMENT.md), [RUNBOOK.md](../RUNBOOK.md)

Line numbers below are for code at main `0dac86fa`.

## 1. Flow

```
Phase 2 task ends (_post_process, phase2 branch)        ecs_entrypoint.py:2413-2436
  provenance gate (_provenance_gate("2"))
  _run_dedup_detection(env), up to 2 attempts
  _phase3_gate_decision(dedup_ok, provenance)           ecs_entrypoint.py:2314
  _notify_dedup_gate(action, reason, held_by)           ecs_entrypoint.py:2663
      auto_proceed -> trigger Lambda invoked with {"phase": "3", "book": ...}   ecs_entrypoint.py:2447-2461
      block        -> nothing is started; wait for a human
Human opens the Dedup Review UI, commits actions, presses "Mark Review Complete"
  POST /dedup/api/complete -> writes dedup/review_status.json, publishes {"dedup_complete": true}
  to {env}-wwii-dedup-complete -> trigger Lambda launches the Phase 3 ECS task
```

When `MULTI_DOC_ENABLED` is on, an `auto_proceed` calls `_advance_doc_lifecycle("2")` and a block calls `_offramp_doc_needs_review(reason)` instead of invoking the trigger Lambda (`ecs_entrypoint.py:2439-2445`, `2069-2084`). `_offramp_doc_needs_review` only sets the doc to `needs-review`. Nothing in `dedup_ui_handler.py` resumes the doc; Phase 3 for the corpus starts from the dedup-complete topic (section 5).

## 2. Detection run

`_run_dedup_detection` (`ecs_entrypoint.py:2760`) runs these steps in order. Every step except the detectors catches its own errors, so one failure does not stop the rest.

| Step | Function | What it does |
|---|---|---|
| 1 | `_reclassify_military_units` (2738) | `src/dedup/reclassify_military_units.reclassify` moves military units out of `places` into groups. Failure: `observe_error("dedup","reclassify_failed")`. |
| 2 | `_cleanup_entity_indexes` (2750) | `src/dedup/cleanup_indexes.cleanup_index` for people (`name`), people_groups (`group_name`), places (`current_name`), equipment (`common_name`). Failure: `index_cleanup_failed`. |
| 3 | `_migrate_exclusions_to_dynamo` (2769) | Downloads the legacy local lists and writes them to DynamoDB through `migrate_local_to_dynamo`. Idempotent. |
| 4 | `_download_dedup_data` (2797) | Materialises all four entity dirs plus `output/content/` (event files) from DynamoDB or S3. |
| 5 | `_reconcile_bibliography_full_corpus` (2834) | Collapses bibliography shards and publishes the `manifests/reconcile_complete/_all.json` barrier that Phase 3 waits on. |
| 6 | `_execute_dedup_scripts` (3279) | Detectors, auto-merge, run markers, report upload (below). |
| 7 | `_maybe_generate_surname_report` (2725) | Surname suggestion report (below). |

### 2.1 `_execute_dedup_scripts`

1. `_generate_places_coords` writes `output/places/coords.json` (distance matching) and `place_coords#<file>` rows in DynamoDB.
2. Four detectors run as `python -m <module>` from `/app` (`ecs_entrypoint.py:3339-3356`). A non-zero exit calls `observe_error("dedup","detector_failed", detector=..., returncode=...)`, and that entity type has no fresh report this run.

   | Module | Writes |
   |---|---|
   | `src.dedup.find_duplicate_people` (`main`, line 1537) | `output/people/duplicate_report.json` |
   | `src.dedup.find_duplicate_places_v2` (512) | `output/places/duplicate_report.json` |
   | `src.dedup.find_duplicate_groups` (472) | `output/people_groups/duplicate_report.json` |
   | `src.dedup.find_duplicate_equipment` (339) | `output/equipment/duplicate_report.json` |

   The people and groups reports have the shape `{total_people|total_groups, duplicate_groups: <int>, duplicates: [<group>, ...]}` (`find_duplicate_people.py:1503-1507`, `find_duplicate_groups.py:444-449`). Each group holds a `people` list (or `groups`) whose entries carry `name` and `filename`.
3. `_auto_merge_exact_duplicates` (section 3).
4. `set_last_dedup_run` records `dedup_run#<entity>` for people, places, groups and equipment (`src/dedup/incremental.py`). It is used by incremental mode (`get_new_files`).
5. Each report is uploaded to `s3://<bucket>/output/<subdir>/duplicate_report.json` (`ecs_entrypoint.py:3366-3375`).

The ECS detector run does not pass a Grok client, so `_verify_ambiguous_matches` (`find_duplicate_people.py:1391`) is not used there. It runs only when a caller supplies `grok_client` to `generate_duplicate_report`.

### 2.2 Other reports

- `output/people_groups/related_groups_report.json` is written by `generate_related_groups_report` (`src/dedup/find_related_groups.py:603`), called from Phase 2 `_run_analysis` (`phase2_extract.py:826-827`). It is not written by the dedup step. The review UI and the gate do not read it. Several tools skip it by name (`dedup_gate_handler.py:143`, `incremental.py:17`).
- `output/people/surname_frequency_suggestions.json` is written by `src/dedup/surname_report.maybe_generate` (`surname_report.py:176`). It regenerates only when the people corpus has grown by at least `dedup.people.surname_report.min_new_people` (default 50) since the last report, tracked in `output/people/.surname_report_state.json`. It is disabled by `dedup.people.surname_report.enabled: false`. It only suggests; it never edits `data/surname_frequency.yaml`. A human promotes entries by hand. Failures are logged at debug and never block dedup.
- The report is not uploaded by `_execute_dedup_scripts`. It reaches S3 through the normal final sync.

## 3. Auto-merge

`_auto_merge_exact_duplicates` (`ecs_entrypoint.py:3138`) runs `_auto_merge_entity_type` (3104) for people (`PersonID`), people_groups (`GroupID`), places (`PlaceID`) and equipment (`EquipmentID`). Each group in the report's `duplicates` list is merged without review (primary = first entry, `merge_generic(entity_dir, people, 0, id_field)`) only in these two cases:

1. Every named entry has the same `normalize_name_ascii` form and the group has at least two entries (`ecs_entrypoint.py:3188`).
2. `_group_cluster_canonically_mergeable` (3166): the directory is `people_groups`, and `_all_keys_match` (3191) finds every record resolves to a canonical unit key with at least one number and `unit_keys_match` against the first record, and `_any_nationality_conflict` (3202, using `find_duplicate_groups._group_nationality_conflict`) finds no conflict. The key rules are in [group-dedup-unit-key.md](../features/people_groups/group-dedup-unit-key.md).

Never auto-merged, so they stay in the report for a human:

- Any non-identical-name group for people, places and equipment.
- Group clusters with a keyless or underspecified name (no number in the key), any veto between a pair, or a nationality conflict.
- Any group where the key check raises. The code returns False on any exception (`ecs_entrypoint.py:3232-3233`).

After merging, the kept groups replace the list, and `duplicate_groups` is reset to `len(remaining)` (3149-3158). The gate reads that integer and the UI reads the list, so they must agree. If they did not, the gate would block Phase 3 while the UI showed nothing to review.

## 4. The gate

### 4.1 Decision

`_dedup_gate_decision(dedup_ok)` (`ecs_entrypoint.py:2633`):

| Condition | Action |
|---|---|
| Detection failed twice (`dedup_ok` false) | block, "dedup detection failed" |
| `_dedup_has_no_pending()` false (2546): any of the four `duplicate_report.json` files missing, unreadable, or with `duplicate_groups > 0` | block |
| All reports present with zero groups, but the entity count is at least `DEDUP_ZERO_SUSPICION_THRESHOLD` (env, default 500) | block, "suspicious zero" |
| Otherwise | `auto_proceed` |

`_count_entities` (2574) counts `*.json` files in the four entity dirs, including `index.json` and report files.

`_phase3_gate_decision` (2265) adds the provenance gate. If provenance validation failed and `PROVENANCE_GATE` mode is `block`, the action becomes `block` and `held_by` gains `provenance`. In `warn` mode the failure is added to the reason text and does not hold. `held_by` is `[]` on auto-proceed, else `["dedup"]`, plus `"provenance"` when it holds. The mode is read by `scripts.validate_provenance.gate_mode`, and an unreadable mode counts as `block` (2239-2250).

### 4.2 Notification

`_notify_dedup_gate` (2614) publishes to `NOTIFICATION_TOPIC_ARN`, which CloudFormation sets to `{env}-wwii-phase2-complete` (`compute.yaml:243-244` and the other task definitions). With no topic ARN it logs `OBSERV dedup_gate.notify ... delivered=no reason=no_topic_arn` and returns.

- Subject: `WWII Pipeline: dedup auto-proceed` on auto-proceed. On a block: `WWII Pipeline: <held_by joined with " + "> review needed` (for example `dedup review needed` or `dedup + provenance review needed`).
- Body: `Dedup gate: <action>`, `Reason: <reason>`, `Bucket: <bucket>`. When the gate blocks on dedup it adds `Phase 3 is blocked until this is reviewed.` and `Dedup Review UI: <DEDUP_REVIEW_URL>`. A provenance hold adds a paragraph about fixing the extraction or setting `PROVENANCE_GATE=warn`.
- Recipients: the topic has an email subscription when the stack has a `NotificationEmail` (`events.yaml:58-64`, config key `aws.notification_email`), the `nat-manager` Lambda (`events.yaml:66-71`), and the `slack-formatter` Lambda, which republishes to the Slack topic (`events.yaml:84-95`).
- `DEDUP_REVIEW_URL` is `https://<DedupApi>.execute-api.<region>.amazonaws.com/app/dedup` (`compute.yaml:542-543`), the same value as the `DedupApiUrl` output (`compute.yaml:1882-1883`).
- Delivery check: after publishing, the code logs `OBSERV dedup_gate.notify action=... delivered=yes|no_confirmed_subscriber`. With no confirmed subscriber it also logs `OBSERV dedup_gate.notify_undelivered` as a warning. The cause is an unconfirmed email subscription or an empty `aws.notification_email`.

### 4.3 Access

The UI is served by `{env}-wwii-dedup-ui` (Lambda, 30 s timeout, `compute.yaml:1176-1190`) behind the `{env}-wwii-dedup-api` REST API. Every route uses the `{env}-wwii-dedup-auth` Lambda authorizer (`compute.yaml:1257-1305`, `1331-1338`). The authorizer compares the `Authorization: Basic <base64>` token with `AUTH_TOKEN`, resolved from the Secrets Manager secret `{env}-wwii-pipeline/dedup-auth`. The secret value is the `user:password` string. A bad or missing token gets a 401 with `WWW-Authenticate: Basic realm="Dedup Review"` (`compute.yaml:1311-1329`).

## 5. How Phase 3 starts

- Auto-proceed: `ecs_entrypoint.py:2447-2461` invokes `{env}-wwii-trigger` with `{"source":"manual","book":<BOOK_NAME>,"phase":"3"}`. A failure to invoke is logged as a warning only.
- After review: `POST /dedup/api/complete` (`dedup_ui_handler._mark_complete`, 1211) sets `complete: true` in `dedup/review_status.json` and publishes `{"dedup_complete": true}` to the topic named in `DEDUP_COMPLETE_TOPIC_ARN` (`{env}-wwii-dedup-complete`). That topic has one subscriber, the trigger Lambda (`events.yaml:242-261`). `_route_topics` (`trigger_handler.py:1464-1473`) handles it: `_stop_phase2_tasks()` and then `_run_task(PHASE3_TASK_DEF, topic_name)`. The task covers all books that passed dedup, not one book. If the launch fails closed (NAT not routable), nothing retries; press "Mark Review Complete" again, or publish the topic message (section 7.4).
- `lambda_handlers/dedup_gate_handler.py` is deployed as `{env}-wwii-dedup-gate` (`compute.yaml:1239`) and updated by `scripts/update_lambdas.sh` and `deploy.yml`. No CloudFormation subscription or event source invokes it, and no other code references it, so the live path is the trigger Lambda above. Its logic: it reads `dedup/review_status.json`, returns `blocked` unless `complete` is true, and on a `dedup_complete` SNS record from `DEDUP_COMPLETE_TOPIC_ARN` it invokes Phase 3 once per record under `output/people`, `people_groups`, `places` and `bibliography`. It rejects records from any other topic (`_is_trusted_dedup_topic`, line 31).
- `dedup/review_status.json` is reset to `{"complete": false, "reviewed": {}}` at the start of every Phase 1 task (`ecs_entrypoint._prepare_phase1`, 1079-1090), so the UI's Complete button (`dedup_ui_handler.py:1345`) is enabled again after new content is parsed. A side effect: a Phase 1 run for a new book also clears the status of a review that was pending or finished for the rest of the corpus. The trigger path does not read the file (see [LOCKS_AND_LEASES.md](LOCKS_AND_LEASES.md)).

## 6. The review UI

Source: `lambda_handlers/dedup_ui_handler.py`. The HTML and JavaScript are the `_HTML_UI` string (line 1272).

### 6.1 Routes

| Route | Handler | Use |
|---|---|---|
| `GET /dedup` | `_serve_ui` | HTML page |
| `GET /dedup/api/groups` | `_get_groups` (306) | Groups from the four report files, de-duplicated by filename (`_dedupe_groups`, 418; groups left with under two files are dropped), sorted by longest name |
| `GET /dedup/api/status` | `_get_status` (1179) | Per-entity `total` and the `complete` flag. `reviewed` is always 0. |
| `POST /dedup/api/action` | `_handle_action` (339) | `merge`, `skip`, `exclude` |
| `POST /dedup/api/undo` | `_handle_undo` (526) | Restore a snapshot |
| `POST /dedup/api/complete` | `_mark_complete` (1211) | Finish the review and start Phase 3 |
| `GET /dedup/api/detail/<entity>/<file>`, `GET /dedup/api/search/<entity>/<query>`, `POST /dedup/api/reclassify`, `/assign`, `/rename` | lines 109-116, 121-152 | Group-card helpers: detail view, search, move an entity between types, assign to a person, fix a filename |

### 6.2 Tabs and card controls

Tabs: `people`, `places`, `groups`, `equipment` (`renderTabs`, line 1363), each with its pending count. Report paths: `output/people/`, `output/places/`, `output/people_groups/` (tab `groups`) and `output/equipment/`, file `duplicate_report.json` (`_report_path`, 1232).

Each card lists the group's records. A radio picks the primary (the merge target, first record by default). A checkbox per record includes or excludes it (`excluded_indices`). Choosing a record as primary re-checks its include box (`ensureIncluded`). Buttons queue an action.

### 6.3 Actions

The request body carries `action`, `entity_type` (`people|places|groups|equipment`), `group_filenames`, `primary_index` and `excluded_indices`. The server finds the group by filename: the first report group whose files contain all named filenames (`_resolve_group_index`, 380). No filenames or no match returns **409** `{"result":"gone"}` and logs `OBSERV dedup.action_group_gone`. It never falls back to another group, so a retried request cannot merge a different one (`dedup_ui_handler.py:355-367`).

| Action | Effect |
|---|---|
| `merge` (`_do_merge`, 437) | Drops excluded records. Fewer than two left: the group is removed from the report, result `skipped`. Otherwise saves a snapshot, merges into the primary, removes the group from the report, and re-clusters any excluded records (two or more) as a new report group (`_re_cluster_and_add`). Returns `{"result":"merged","primary":<name>,"snapshot_id":...}`. |
| `skip` (`_do_skip`, 401) | Records `reviewed#<entity>#<fileA>#<fileB>` (files sorted) for every pair in the group in DynamoDB with `reviewed_at` and a `ttl` 90 days out (`_record_reviewed`, 1124), then removes the group from the report. |
| `exclude` (`_do_exclude`, 848) | Writes the group to the exclusion store: `exclusion#<entity>#<fileA>#<fileB>` per pair and `name_exclusion#<entity>#<normA>#<normB>` per pair of normalised names (`ExclusionStore.add_group`, `add_group_by_name`; key builders in `src/dedup/exclusions.py:44-65`). Then removes the group from the report. |

Merge mechanics:

- Generic types (`_merge_generic_files`, 576): unions `event_mentions` into the primary with `merge_mentions`, appends each secondary's name to `aliases`, writes the primary first, rebases references, then deletes the secondary file. If the Lambda has under 10 s left, `_absorb_secondaries` stops and the merge is partial.
- People (`_merge_people_files`, 757): downloads the files to a temp dir, runs `src.dedup.merge.do_merge`, rebases, then uploads and deletes (`_upload_merged`, 832). A merge with fewer than two files still present returns `skipped`.
- Both append the changed keys to `manifest#phase2` in DynamoDB (`_append_to_manifest`, 56).

### 6.4 Rebase of references

Before a secondary file is deleted, the handler repoints event sub-event references from the secondary ID to the primary ID in every `output/content/**/*-event.json` (`_rebase_event_refs_storage`, 195). The reference key per type is `people`/`people`, `places`/`places`, `groups`/`peoplegroups`; equipment has no sub-event array and is skipped (`ENTITY_PK_AND_REFKEY`, 168). Log lines:

- `OBSERV dedup.rebase.ok ref_key= old= new= refs=<n>`
- `OBSERV dedup.rebase.zero` (warning) when no event referenced the old ID. That is expected when the entity was never referenced and an error when the key or ID is wrong.
- `OBSERV dedup.rebase.noid` and `dedup.rebase.list_failed` when no refs were repointed, and `dedup.rebase.write_failed` (`observe_error`) when an event file could not be written.

Auto-merge in the ECS task uses `src.dedup.merge` and its own `OBSERV dedup.rebase.*` lines (`merge.py:600-650`).

### 6.5 Commit All queue (browser)

- Clicking an action only queues it. The queue is `localStorage["dedupPending"]` and survives a reload (`dedup_ui_handler.py:1399-1402`). A bar shows "N action(s) queued" with Clear and Commit All.
- "Commit All" (`commitAll`, 1437) posts the queued actions one at a time, oldest first. An action leaves the queue and the queue is saved only after the server answered it, with success or 409 (1457-1465). A 409 means the group is already gone (committed) and is dropped.
- On a network error (no response) or a 5xx, the remaining actions stay queued. The browser stores `dedupRetryAfter = now + 35 s` in `localStorage` and refuses to commit before then (1436-1441, 1455, 1460). The wait outlasts the 30 s Lambda timeout, so a first request that is still running finishes before the retry. After it, a repeated action returns 409 and is dropped, so no merge runs twice. Other 4xx errors stop the run without a wait.
- Merge snapshot IDs from a commit are kept in the page and shown in an "Undo Last" bar (`showUndoBar`, 1475). They are not saved across a reload.

### 6.6 Undo

Each people/generic merge calls `_save_merge_snapshot` (494) before changing anything. It copies each file of the group to `dedup/history/<snapshot_id>/<entity>/<filename>` and writes `dedup/history/<snapshot_id>/manifest.json` (`entity_type`, `prefix`, `files`, `timestamp`). The ID is the millisecond timestamp. `POST /dedup/api/undo` with `{"snapshot_id": ...}` (`_handle_undo`, 526) rewrites each listed file to its original path and returns `{"result":"undone","restored":<n>}`; a missing `snapshot_id` is 400 and an unknown one 404.

Undo restores the files only. The code does not revert the event-reference rebase, the `index.json` entries, `manifest#phase2`, or the report group. After an undo the restored pair is not in the report, so it can reappear in the next detection run unless it is excluded.

### 6.7 How skips and exclusions stop reappearing

| Source | Where read | Reaches |
|---|---|---|
| `exclusion#<entity>#<a>#<b>` and `name_exclusion#<entity>#<a>#<b>` rows | `get_exclusion_store(...).load()` and `.load_name_exclusions()` | people (`find_duplicate_people.py:1063-1068`, `_is_pair_excluded` 1171), groups (`find_duplicate_groups.py:50`, `_filter_excluded` 149), places (`find_duplicate_places_v2.py:197`), equipment (`find_duplicate_equipment.py:248`) |
| `reviewed#<entity>#<a>#<b>` rows (90-day TTL) | `load_reviewed_pairs(entity)` (`exclusions.py:388`; returns an empty set when AWS is off) | groups (`find_duplicate_groups.py:100-115`), places (`_drop_fully_reviewed`, 237), equipment (`find_duplicate_equipment.py:275`) |

- A group is hidden only when every pair in it is excluded (groups, `_filter_excluded`) or reviewed.
- The people detector does not read `reviewed#` rows. A people group that was skipped can come back in the next run, and only an Exclude is permanent for people. (From the detector code; not reproduced in a run.)
- Name-based exclusions survive re-extraction under a new filename. File-based ones do not.
- A failed exclusion read raises `ExclusionLoadError` rather than returning an empty set (`exclusions.py:24`), so the detector aborts instead of re-offering curated pairs.
- Legacy local lists (`output/people/not_duplicates.json`, `places/not_duplicates.json`, `people_groups/not_related.json`, `equipment/not_duplicates.json`) are migrated into DynamoDB by step 3 of the detection run. `not_people.json` is a different list: regex patterns the extractor uses to drop non-person names (`src/extraction/batch_parallel.py:188-199`). The UI does not touch it.
- `scripts/e2e_reset_data.py` never touches the `exclusion#` and `name_exclusion#` rows. With `--full` it also deletes the `reviewed#` rows (`e2e_reset_data.py:19-22`, `59`).

## 7. Operator runbook

### 7.1 Review

1. The alert arrives by email and in Slack: subject `WWII Pipeline: dedup review needed`, with the `Dedup Review UI:` link. The same URL is the `DedupApiUrl` stack output.
2. Open it and sign in with the Basic-auth user and password from the secret `{env}-wwii-pipeline/dedup-auth`.
3. Work through the four tabs. For each group choose the primary, untick records that are not the same entity, then Merge, Skip (not sure, ask again later), or Exclude (never the same).
4. Click Commit All. Wait for the "N merge(s) committed" bar.
5. When the tabs are empty (or you accept what is left), click Mark Review Complete and confirm. Phase 3 launches from the dedup-complete topic.

If the alert says `provenance` held the book, no review in the UI releases it. Fix the records and re-run, or set `PROVENANCE_GATE=warn` (alert text, `ecs_entrypoint.py:2306-2310`).

### 7.2 Network error or timeout

Do not reload and re-click quickly. The first request may still be running for up to 30 s. The queue is saved in your browser. Wait the 35 s the page asks for, then press Commit All again. Items already committed return 409 and are dropped. If it keeps failing with a 5xx, check the `{env}-wwii-dedup-ui` log group for `OBSERV dedup.*` lines before retrying. Queue state is per browser profile. A different browser starts with an empty queue.

### 7.3 Undo a wrong merge

1. Right after committing: use Undo Last in the page.
2. Later: find the snapshot with `aws s3 ls s3://<bucket>/dedup/history/` (the folder with `manifest.json` that lists the files), then `POST /dedup/api/undo` with its `snapshot_id` (curl example in [RUNBOOK.md](../RUNBOOK.md)).
3. After an undo, exclude the pair so it does not reappear, and check event references: refs rebased to the primary stay on the primary (section 6.6).

### 7.4 Force-skip the gate

- Release Phase 3 without reviewing: publish `{"dedup_complete": true}` to `{env}-wwii-dedup-complete` (command in [RUNBOOK.md](../RUNBOOK.md), "Force-skip dedup review"). It starts the Phase 3 task through the trigger Lambda (section 5). It does not write `review_status.json`.
- Or press Mark Review Complete in the UI with groups left. They stay in the reports.
- Change what auto-proceeds: raise `DEDUP_ZERO_SUSPICION_THRESHOLD` (default 500) to stop the "suspicious zero" block. There is no switch that skips dedup detection.
- Provenance hold: set `PROVENANCE_GATE=warn`.

## 8. Observability

| Signal | Where |
|---|---|
| `Dedup gate: <action> — <reason>` (info) | `ecs_entrypoint.py:2435` |
| `OBSERV dedup_gate.notify`, `dedup_gate.notify_undelivered` | `ecs_entrypoint.py:2670-2714` |
| `observe_error dedup detector_failed / reclassify_failed / index_cleanup_failed` | `ecs_entrypoint.py:3352`, `2747`, `2766` |
| `OBSERV dedup.action_group_gone`, `dedup.report_unreadable`, `dedup.rebase.*` | `dedup_ui_handler.py:356`, `390`, `195-240` |
| `OBSERV dedup_gate.untrusted_topic` | `dedup_gate_handler.py:56` |
| Log groups `/aws/lambda/{env}-wwii-dedup-ui`, `-dedup-gate`, `-dedup-auth` | `compute.yaml:196-208` |
| Status files | `dedup/review_status.json`, `scripts/utils/pipeline_status.sh` and `pipeline_watch.sh` print it |

## 9. Related

- The provenance gate (`PROVENANCE_GATE` / `provenance_validation.gate`, default `warn`) that can hold Phase 3 after this review: [../dataquality/PROVENANCE_AND_INTEGRITY_GATES.md](../dataquality/PROVENANCE_AND_INTEGRITY_GATES.md).
- How alerts on the `{env}-wwii-phase2-complete` topic reach email and Slack: [../MONITORING.md](../MONITORING.md).
