# CloudFormation security review — 2026-10-11

Full security review of all `cloudformation/*.yaml` (owner-requested), via parallel security
sub-agents by template group. **Result: 0 CRITICAL, 3 HIGH, 6 MEDIUM** (+ low/info). No public
ingress, no wildcard IAM trust/assume, no plaintext secrets, no open bucket/topic/queue policies —
the architecture is sound; these are hardening items. Confirmed already-good: S3 (SSE-AES256 +
BucketKeyEnabled + all public-access-block + DenyInsecureTransport + versioning + Retain), GPU EBS
encryption, OIDC trust pinned to `repo:<org>/<repo>:ref:refs/heads/main`, ECS task/exec role
separation, no 0.0.0.0/0 ingress, GROK key via Secrets Manager (not plaintext), the new
`BackstopRole`/`S3ValidationBackstop` dedicated least-priv roles (the model to copy).

## HIGH
1. ✅ **DONE (PR #347)** — **chatbot.yaml:SlackChannelConfig — no `GuardrailPolicies`** → AWS Chatbot
   defaulted the channel ceiling to **AdminAccess**. Fixed: `GuardrailPolicies: [arn:aws:iam::aws:policy/ReadOnlyAccess]`
   (the ChatbotRole is already read-only; alerts never need write/command actions). cfn-lint PASS.
2. **iam.yaml:GitHubActionsDeployRole — `iam:PassRole` on `${env}-wwii-*` with no
   `iam:PassedToService` condition**, in the same statement as `CreateRole`/`PutRolePolicy` over the
   same wildcard → CI-token privilege-escalation primitive (bounded to the dev-wwii namespace).
   FIX: split PassRole into its own statement with `Condition StringEquals iam:PassedToService`
   (lambda/ecs-tasks/scheduler/states); require an `iam:PermissionsBoundary` on CreateRole/PutRolePolicy.
3. **compute.yaml — internet-facing DedupAuth/DedupUI/MdReviewUI/Metrics Lambdas share the broad
   `LambdaExecutionRole`** (ec2 NAT `*`, dynamodb `${env}-wwii-*`, SNS, ecs RunTask/PassRole).
   FIX: give each public-facing Lambda its own minimal role (model: `BackstopRole`).

## MEDIUM
4. **storage.yaml — 10 entity DynamoDB tables lack PITR** (only CacheTable has it); both roles hold
   DeleteItem/BatchWriteItem on `${env}-wwii-*` → durable extracted output is unrecoverable from a
   wipe. FIX: add `PointInTimeRecoverySpecification{PointInTimeRecoveryEnabled: true}` to each.
5. **iam.yaml:GitHubActionsDeployRole — `secretsmanager:GetSecretValue`** the deploy role doesn't
   need. FIX: remove (or scope to an exact secret ARN if a deploy-time dynamic ref needs it).
6. **events.yaml:TriggerQueue — no SSE + no DLQ** (BatchSize 100 → poison batch retried 14 days).
   FIX: `SqsManagedSseEnabled: true` + `RedrivePolicy` → a dedicated trigger DLQ (maxReceiveCount ~5).
7. **ocr.yaml:OcrSpotControllerRole — `batch:SubmitJob`/`TerminateJob` on `*`** (GPU cost-abuse /
   terminate-any-job if the controller Lambda is compromised). FIX: scope SubmitJob to the chandra
   queues + chandra/paddle job-defs; Terminate/Describe to the chandra queue ARNs (ListJobs stays `*`).
8. **compute.yaml:DedupApi + MetricsApi — internet-reachable** (no resource policy / not PRIVATE)
   exposing review/mutation Lambdas behind one shared static password. FIX: API GW resource policy
   (SourceIp / VPCE) or PRIVATE endpoint; rotate the basic-auth secret.
9. **compute.yaml:DedupAuthFunction — non-constant-time password compare + no throttle/lockout**
   (secret itself correctly from Secrets Manager). FIX: `hmac.compare_digest`; per-user tokens;
   WAF/stage throttle.

## LOW / INFO (track, not urgent)
- SNS topics + TriggerQueue unencrypted at rest (add `KmsMasterKeyId: alias/aws/sns` / SQS SSE).
- `SlackTopicPublishPolicy` allows `:root` publish (scope to the formatter role).
- Shared Lambda/ECS egress `-1` to `0.0.0.0/0` (known posture for scrapers/downloaders).
- `dispatcher.yaml` state machine has no `LoggingConfiguration` (observability).
- `AvFreshclamSchedule` uses the task's own role as the EventBridge invoke role (use a dedicated one).
- Confirm the IaC `TemplateBucket` has the same private + DenyInsecureTransport posture as the data bucket.
- Explicit `SSESpecification` on DynamoDB tables (default AWS-owned-key encryption applies today).

These are tracked for a security-hardening pass; none block current operation (dev account, no
public ingress, no open resource policies, no plaintext secrets).
