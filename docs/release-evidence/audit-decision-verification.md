# Specific audit decisions and bounded live verification — 2026-09-30

## Original failure

Run `00358c77-3efb-42ea-bbcc-2914b5f350ad` analyzed all five iPhone 17 Pro Max reviews and retained 51 validated quotation references. It spent 12 chat calls, 92,242 tokens and 93.67 seconds, then remained unpublished. Both audits rejected all twelve findings, including directly matching quotations. Correction kept all twelve finding statement/source/citation identities unchanged and changed only usage-duration fields. Synthesis spent one binding-invalid retry. The reconstructed audit request matched its saved hash; cited excerpts reached the auditor. The old output contract did not retain individual rejection reasons.

`backend/tests/fixtures/iphone_audit_rejection.json` captures sanitized structured drafts, the ineffective correction, bounded linked excerpts, source metadata, issues and baseline contracts. UUIDs are anonymized. Complete transcripts, secrets, raw provider responses and hidden reasoning are excluded.

## Implemented changes

- Publish `DecisionAuditorInput` / `FindingAuditResult`: exactly one check for each original finding path. Rejections require a category, relevant finding-owned evidence, exact unsupported clause and bounded explanation. Code validates coverage, paths, references, ownership and spans, then projects to the unchanged `AuditResult`. Internal diagnostics preserve concise decisions and rejection reasons.
- Use input-specific response schemas with exact path enums/counts and separate positive/negative shapes. Positive rejection fields must be JSON null; negative fields must be nonempty. Missing or malformed decisions fail validation. Missing paths and unknown/duplicate path counts are diagnostic data; raw model outputs remain unretained.
- Publish `RepairSynthesisInput` / `EvidenceBoundBuyingSynthesis`: derive the one assertion owner from its citations. Remove redundant source selection. Per-input generation schemas constrain a citation list to one source's authorized references. Unknown/mixed ownership remains rejected in code.
- Supply precise rejected finding repair targets and reject unchanged statement/source/citation identities before spending a re-audit call. Changing duration, order or summary cannot satisfy this check. Require cited conditions, reviewer attribution and quantities; no invented drop causes or uncited six-month condition.
- Preserve all known older compiled review, curator, synthesis and auditor adapters; public API fields, scoring, caching, twelve-assertion cap, budgets and one-correction/one-re-audit limits remain unchanged. Failed final audits remain unpublished.
- Golden checks now require preserving the supported finding as well as rejecting the deliberately unsupported narrative. Rejecting every supported finding cannot pass this gate. Golden execution uses the same constrained generation schemas as analysis execution.

## Three authorized live analyses — acceptance remains unmet

Exactly three fresh runs were admitted through the public API, without retrying admission. Every recorded chat call used `deepseek/deepseek-v4-flash` via OpenRouter. Requested source counts and comment settings match the saved options. No fourth analysis was launched.

| Product | Run | Sources | Calls | Tokens | Seconds | Actual outcome |
|---|---|---:|---:|---:|---:|---|
| iPhone 17 Pro Max, comments off | `75e78b15-24f6-4b26-a33b-cd979e6c04c7` | 5/5 | 10 | 82,638 | 78.78 | Failed, unpublished |
| HP Omen 16 Max, comments on | `3ef0e45f-d32d-49ee-9bd1-f2fb4ff572bb` | 5/5 | 14 | 115,858 | 79.65 | Failed, unpublished |
| Blackshark T11, comments off | `5ff56c8f-45ad-4bed-a3cf-ec44e9f3b9cd` | 4/5 | 9 | 48,015 | 84.74 | Published partial, `pass_with_warnings`; full acceptance failed |

### iPhone diagnostics and subsequent fix

The first audit response failed exhaustive path coverage. Its retry failed consistency validation on all twelve decisions. Neither audit produced validated semantic decisions, so correction/re-audit did not run and publication failed with `report_audit_missing`. The exact malformed output is unavailable because raw retention is disabled; a shortened path or nonnull positive explanation cannot be claimed as the exact historical output. Input-specific path/count constraints, positive/negative schema branches, precise validator codes and bounded coverage diagnostics were added after this failure. No new iPhone run tested those fixes within the cap; iPhone live acceptance remains unresolved.

### HP diagnostics and subsequent fix

Both synthesis attempts failed `assertion_source_mismatch` at `assertions[0]`. No valid draft reached the auditor. Publication remained blocked. Citation lists were subsequently constrained in the generation schema to one authorized owner. HP was not rerun within the cap, so its live acceptance remains unresolved.

### Blackshark diagnostics and remaining defects

The final test used both constrained schemas. A caption request failed `transcript_access_blocked`; four source analyses remained usable. The first synthesis attempt failed reference validation; the retry produced a bound draft. The auditor supplied all twelve indexed positive decisions and one specific narrative rejection: the supplied finding citations did not establish the summary's app-support statement. Safe pruning replaced that summary, recorded a warning and published without a correction/re-audit model call.

The published report contains twelve strengths and ten independently validated product facts, including `3.2 g`, `30 hours` and Bluetooth `5.3`. However, it retained **zero caveat findings** despite source quotations about bass limitations and loose-case durability concerns. Several assertions also cite an unnecessarily broad set of same-source excerpts. Its summary pruned material drawback prose, and some buyer guidance was accepted without matching retained caveat findings. Therefore publication is verified, but the requested retention of material drawbacks and general semantic accuracy are **not** verified. This is an unresolved acceptance failure, not a passing live benchmark.

[Published Blackshark report](http://localhost:3000/r/Kl3-SmfWcWUKvnMa5CkYvuk3TwjdlU8eOdx0u6x8v_o). The completed report is unchanged; it is not promoted as an accepted result. Uncached caption availability remains subject to YouTube blocking.

## Offline and worker evidence

- Final local backend suite: **246 passed, 58 isolated-stack tests skipped**. The final isolated full suite passed **300 tests**, with four host-only gates skipped, including the per-input schema refinements and matching golden-evaluation schemas. Ruff and focused five-module Mypy checks passed; the context audit covers 28 notes. Existing deprecation warnings are unchanged.
- Seven real worker fixture workflows passed: normal complete (9 calls), comments (14), lower/uppercase recoverable audit (11 each), failed re-audit (11, unpublished), malformed empty correction (10, no re-audit, unpublished), unchanged correction (10, no re-audit, unpublished). Curator and separate product-information model calls were zero. Context isolation, projection idempotency and transaction rollback checks passed.
- Fixtures supply scripted audit decisions. These results demonstrate workflow handling and publication safety, not live model audit accuracy.
- Twenty matched current-contract replays, three repetitions each, passed all call-count, evidence, source/topic coverage, estimated total-token and local p95 timing gates. Five pairs include correction and re-audit; retries are counted. The baseline retains the captured contracts and ineffective correction behavior. Candidate outputs are human-labelled narrower observations, not fresh model generations.

| Offline measure | Captured baseline | Final constrained candidate |
|---|---:|---:|
| Mean estimated total tokens | 89,287 | 78,462.25 |
| p95 estimated total tokens | 89,287 | 85,456 |
| p95 local completion | 123.48 ms | 108.53 ms |

These measurements combine captured common-call usage, changed prompt/schema/completion estimates, fixed 2 ms fixture delays and local application processing. They include repair work but exclude remote inference, production queues and network time. Three live samples do not establish production token or latency parity.

## Rollout

API, worker, scheduler and migration images were rebuilt and restarted between the discovered schema fixes. Actual active workflow `62264884-ad8c-41a5-986d-7283d293816a` (version 25) binds Consensus Analyst 16 (`RepairSynthesisInput` → `EvidenceBoundBuyingSynthesis`) and Quality Auditor 16 (`DecisionAuditorInput` → `FindingAuditResult`). All eight saved model choices and every executable agent binding use DeepSeek V4 Flash. Readiness confirmed PostgreSQL, Redis, Markdown storage, Neo4j and Qdrant. All completed run records and database volumes are preserved.

**Release acceptance is not complete:** iPhone/HP fixes require fresh live retests, and Blackshark still omits material drawbacks. The paid three-analysis cap has been exhausted.

## Follow-up: the user's no-comments and comments runs

### Verified logs

- `03ae1532-b883-4a8e-b5ac-ce62adee9675` (iPhone 18 Pro Max, comments off): five validated source analyses, ten chat calls and 76,125 tokens. The first audit failed an unknown/mismatched narrative path; its retry accepted all twelve findings. Deterministic grounding removed only `consensus_pros[4]`: “0 to 81% in 30 minutes,” although its verified GSMArena excerpt says “0 to 81% in half an hour.” Eleven findings were published with a quality warning. This is a confirmed false numeric rejection, not a source shortfall.
- `3a6d1817-ce31-482c-a2ce-f729f9180f17` (Samsung S25 Ultra, comments on): all five review analyses and all five aggregate audience analyses succeeded. Both synthesis attempts failed `synthesis_reference_invalid`; no valid draft reached audit, and publication failed `report_audit_missing`. Fourteen calls consumed 115,503 tokens, all on DeepSeek V4 Flash. The historical validator retained only generic `reference_invalid`, and raw responses remain disabled. Its exact malformed reference cannot be reconstructed.

### Corrections and limits

Explicit hours/minutes/seconds now compare exact rational durations independently of other quantities. Wrong units, percentages, rounded values and foreign-source support remain rejected. The captured charging statement survives a read-only replay of the **actual saved model decisions**, retaining all twelve findings with audit `pass`. Stored report/publication/usage fingerprint `73d2c26f0100e82e646677bfba1f9fb912b4da381a05b13f678b1b456fe7cd3e` is identical before and after rollout.

Report/PDF presentation maps internal limitation aliases to the numbered public source and reviewer without rewriting stored records. The current page and PDF return HTTP 200; completed records keep their original omission and warning.

`CatalogRepairSynthesisInput` uses one source-reference namespace for review excerpts and aggregate comment signals. It strips source/audience-node UUIDs from model-facing signals while retaining sentiment, recurrence and sampling limitations. Earlier `RepairSynthesisInput` and other compiled contracts remain supported. Disagreement citation arrays and duration-source references now have catalog enums. Repeated authorized citations are deduplicated in code without a synthesis retry; unknown or mixed ownership still fails with an indexed field diagnostic. The prose guard permits product code `S25` while rejecting actual short catalog labels. Narrative audit schemas constrain existing paths and their issue categories.

These changes cover the previously generic binding failure branches; duplicate citations and malformed optional references in regression tests are **fault injections**, not claimed reproductions of the unavailable historical model response. Completed user runs have not been retried or republished.

### Verification and deployment

- Local backend suite: **257 passed, 58 isolated-stack tests skipped**. Isolated suite: **311 passed, four host-only gates skipped**. Ruff and focused nine-module Mypy checks passed; the 28-note context audit passed.
- Seven worker fixture workflows passed. Normal and comments-enabled runs published five-source reports with 9 and 14 mock model calls respectively. The comments fixture deliberately repeats valid citations, completes without a synthesis retry, and performs zero curator/product-information model calls. Failed final audits and failed/unchanged corrections remain unpublished. All fixture runs recorded zero live calls.
- Twenty matched captured audit replays, three repetitions per case, passed evidence/source/topic, call-count and estimated-budget gates, including correction/re-audit work. Candidate mean/p95 estimated tokens were 78,708.5 / 85,850 versus 89,287 / 89,287 baseline; local p95 completion was 109.06 ms versus 124.94 ms. These are local scripted replays, not remote inference or production parity measurements.
- Main API, worker, scheduler and migration images were rebuilt. All eight running services are healthy. Active workflow **26**, `4c064915-be91-4dca-b122-06ee580008d1`, binds Consensus Analyst **17** (`CatalogRepairSynthesisInput` → `EvidenceBoundBuyingSynthesis`) and Quality Auditor **16** (`DecisionAuditorInput` → `FindingAuditResult`). Every executable model binding remains `deepseek/deepseek-v4-flash`.

The final deployment replay caught a regression introduced while adding compact `h`/`s` units: optional spacing allowed the word “as” to parse as one second. Word-valued amounts now require a separator; numeric shorthand remains supported. Regression checks for “Stated as 5.3” and “Stated as 3.2 g” pass. After that final domain-only change, all 257 local tests were rerun successfully, the final running API replay retained all twelve actual findings, and readiness/report/PDF checks passed. The isolated 311-test/worker suite preceded this small parser amendment; no subsequent full-stack rerun is claimed.

No fresh paid analysis was launched during this follow-up. End-to-end success of a new live comments-enabled generation remains unverified; the prior three-analysis cap remains exhausted.
