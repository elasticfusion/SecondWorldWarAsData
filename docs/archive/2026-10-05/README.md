# Archived 2026-10-05 (documentation audit)

Moved out of `docs/current/` during a full documentation audit. Each was either a
dated point-in-time snapshot or a rejected/unimplemented approach. History preserved
via `git mv`.

| Doc | Why archived | Superseded by / notes |
|---|---|---|
| `PHASE3_REVIEW.md` | 2026-10-02 read-only enrichment audit (point-in-time) | Top findings fixed (geocoding PR#217, failure-visibility PR#221, M3/H3 PR#222, award sourcing). **Unresolved findings carried into `docs/current/TODO.md` → High Priority.** |
| `SUPPLEMENTARY_SEARCH_REVIEW.md` | 2026-10-02 read-only per-search audit (point-in-time) | **Unresolved findings carried into `TODO.md`** (Grokipedia raw-HTML, fail-open verifiers, etc.). |
| `eto_oob_division_coverage_report.md` | Point-in-time coverage measurement of one OOB extraction run | Regenerate from `scripts/audit_eto_oob_division_coverage.py` when needed. |
| `ISOLATION_AUDIT.md` | 2026-09-28 prerequisite snapshot for multi-doc parallelism | Gaps G1/G2/G4 addressed in code; superseded by `dataquality/INTAKE_FRONT_DOOR_STATE.md` + `CONCURRENCY_AND_NAT_SPEC.md`. |
| `UNATTENDED_READINESS.md` | 2026-09-28 readiness snapshot | Most CRITICAL gaps fixed (OCR→parse wiring, Spot, Slack); superseded by `INTAKE_FRONT_DOOR_STATE.md`. |
| `CODE_REVIEW-2026-06-13.md` | Dated code-review snapshot ("284 passed"; now 1387 tests) | One-time review artifact. |
| `PROMPT_AND_MODEL_RECOMMENDATIONS.md` | 2026-06-14 advisory memo ("no code changes made") | Recommendations partly actioned via `model_map`; backlog in `TODO.md`. |
| `TAILSCALE_EXIT_NODE.md` | "Planned" residential-egress approach — **never built, rejected** | Proven unnecessary; no infra/code trace. See `AWARD_SOURCING.md` access findings (full browser headers from AWS suffice). |
| `PROTONVPN_EXIT_NODE.md` | "Planned" commercial-VPN egress alternative — **never built, rejected** | Same as above. |
