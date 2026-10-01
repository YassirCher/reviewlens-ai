# Blackshark audit failure repair

## Verified failed run

Run `24317c66-c7da-4223-901b-fefaa6a5a133` used workflow 29 and DeepSeek V4 Flash. All five video analyses succeeded. Four audience model calls completed; one comments-unavailable branch completed without a model call.

- Actual total: **14 chat calls, 77,011 tokens, 84.36 seconds**.
- Both audit attempts failed `audit_decisions_invalid`: the rejection clause did not match its finding.
- No valid audit reached correction. Publication failed with `report_audit_missing`; no report was published.
- Synthesis repeated the same unfinished sound-quality strength eight times. Its observation ended with “and no audible”; the whole finding combined several attributes and attributed one source's excerpts to multiple reviewers.
- Invalid auditor responses were not retained. The precise rejected clauses and semantic reasons are unavailable. Paraphrasing or translation of a clause is a possible explanation, not a confirmed observation.

Dependency readiness did not establish analysis correctness. Earlier fixture tests prescribed audit decisions and could not discover this live rejection-format failure.

## Implemented correction

`PartAuditorInput` supplies ordered, lossless text parts of at most 200 characters. `ReferencedAuditResult` selects an owned part reference rather than generating rejection wording. Code resolves the exact original text and retains decision coverage, original indexed paths, evidence ownership, rejection categories, narrative scope and exact-span validation. The public `AuditResult` and correction targets are unchanged. Unknown or foreign references still fail.

`DistinctBuyingSynthesis` removes exact normalized repetitions only when their owner, kind, attribute, observation, conditions and citation set match. Source-specific, opposing and differently conditioned assertions remain distinct. Bounded diagnostics identify original indices. The synthesis prompt requests complete short observations without slot filling; the auditor checks unfinished assertions and plural attribution.

Legacy `NormalizedBuyingSynthesis`, `DecisionAuditorInput` and `FindingAuditResult` adapters remain compiled for immutable snapshots. Existing failed/completed runs are preserved. No call allowance, configured token limit, retry allowance or publication gate is raised.

The sanitized captured draft and validated excerpts are in `backend/tests/fixtures/blackshark_audit_format.json`. Review type is normalized to unknown in this formatting fixture; it contains no raw auditor response or individual audience comment body. Supplied test decisions are labelled fixtures, not live model outputs.

## Verification status

- 187 targeted backend regressions passed before two additional edge cases; all 20 rejection-reference regressions subsequently passed.
- Ruff checks and mypy for the six affected runtime modules passed.
- Context audit passed.
- 24 matched offline replays passed, including all measured retry/correction/re-audit work. Candidate estimated mean tokens were **92,278.42**, versus **92,494.67** at baseline. Valid source coverage was unchanged and tested unsupported finding retention did not increase.
- Local p95 validation/envelope processing was **42.76 ms candidate / 8.76 ms baseline** in this measurement. This is local processing, not model or production completion latency. Production token and latency parity remain unverified.
- Full isolated backend suite: **375 passed, 5 skipped**. The first execution found a stale prompt-string assertion; it was updated to the current explicit policy and the complete suite rerun successfully.
- Seven isolated worker scenarios passed their expected outcomes with five source analyses and zero live provider calls. Valid normal/comments runs published with **9/14 calls**; eligible corrections used **one correction plus one re-audit (11 calls)**. Failed final audit remained unpublished (11 calls); empty or unchanged correction prevented re-audit/publication (10 calls). Product extraction and graph projection each used zero model calls.
- Deployed binding verification passed for API, worker and scheduler: all affected file hashes match the workspace; actual active model bindings are DeepSeek V4 Flash. Configured budget hash, agent generation/execution limits and task retry allowances match the pre-change capture.

## Runtime restart

After the laptop restart, Docker Desktop failed with its previously observed stale `dockerInference` socket error. The exact runtime socket folders were moved to timestamped backups, then Docker restarted. No factory reset or application volume removal occurred. The idle main stack was stopped while isolated tests ran to stay within the Docker VM's 4 GB memory limit.

The main API, worker, scheduler and migration images were rebuilt and the app relaunched. API live/readiness and the frontend returned HTTP 200. Active workflow **30** uses synthesis/correction version **20** (`DistinctBuyingSynthesis`) and audit/re-audit version **19** (`PartAuditorInput` / `ReferencedAuditResult`), all bound to `deepseek/deepseek-v4-flash`. Normal calls remain nine without comments or fourteen with five audience analyses, with the existing bounded retry and repair allowance retained.

The failed Blackshark record was compared before/after deployment and is unchanged, including its failed status, timestamps, options, publication absence and 14 calls / 77,011 tokens. Four earlier completed records were also verified unchanged. Existing historical Markdown quarantine remains unchanged.

Local ignored evidence is in `.audit-cache/audit-parts-stack-final.log`, `reliability-stack-result.json`, `reliability-deployed-final.json`, `reliability-budget-final.json`, `audit-parts-failed-record-preserved.json` and `synthesis-reliability-replays.json`.

## Live acceptance boundary

The previous exactly-two-analysis paid allowance was already consumed by the iPhone and Samsung examples documented in `synthesis-reliability-2026-10-01.md`. This repair has not yet had a new paid analysis. Offline/fixture success must not be described as DeepSeek live success or a guarantee that future research will complete.
