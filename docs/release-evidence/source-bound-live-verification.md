# Source-bound synthesis correction and live verification

Verified on 2026-09-30. The user authorized up to three fresh analyses using
`deepseek/deepseek-v4-flash`. Exactly three were submitted through the public
preflight and admission API, requesting five videos, English, and no comments.
Existing run budgets, source eligibility, caption caching, and publication gates
remained in force.

## Reproduced failure

Run `984e1d63-c98e-4530-b831-f7e36299c435` analyzed five sources but failed
`publish_report` with `report_audit_failed`, recording 83,139 tokens. Its first
synthesis grouped different reviewers' observations into twelve compound
findings, all labelled caveats. Internal catalog labels such as `s1` appeared in
finding prose and were interpreted as product codes by deterministic scope
validation. The auditor also rejected unsupported compound clauses and source
attribution. Correction repeated essentially the same rejected findings; the
final audit correctly blocked publication.

## Correction

- Publish `SourceBoundBuyingSynthesis`: each assertion names one short source
  reference and cites evidence owned by that source. Mixed ownership, unknown
  references, and catalog labels in assertion prose produce indexed validation
  issues. Stored UUID bindings remain server-side.
- Clarify strength/caveat labels, complete short observations, separate source
  results, and rebuilding rejected findings in the synthesis prompt.
- Retain compiled `AtomicBuyingSynthesis`, `BuyingSynthesis`, and original report
  adapters for immutable snapshots. Public report fields remain unchanged.
- Keep nine normal chat calls and the existing one-correction/one-re-audit limit.
  Failed final audits remain unpublished.
- Independently reject explicitly labelled comparison-model identity facts even
  when their scope is missing and their excerpt is verbatim. This was detected
  in the first live report; the third run verifies the final filter.

The full stored-text fixture export was rejected by automatic approval review.
Regression tests use the existing sanitized five-source evidence fixture and
small synthetic reproductions instead. Raw model response retention was not
enabled. Existing completed and failed run records remain unchanged.

## Live results

| Test | Run ID | Valid sources | Chat calls | Tokens | Duration | Publication |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `95f5983c-8ac0-4da1-8185-8bf6cd5105de` | 5/5 | 9 | 56,713 | 64.09 s | Published with evidence warnings |
| 2 | `3c957972-bbea-44db-bc9c-576729b35cec` | 5/5 | 9 | 55,893 | 64.11 s | Published with evidence warnings |
| 3 | `65f13013-589c-4379-aaf0-94a6f04235db` | 5/5 | 9 | 56,712 | 72.09 s | Published with evidence warnings |

Every recorded chat invocation used the requested DeepSeek model. Each report
retained eleven findings, with respective strength/caveat counts of 6/5, 8/3,
and 7/4. No correction or re-audit model call was needed: those tasks recorded
their passing-audit shortcuts. Their final audit verdicts were
`pass_with_warnings`; unsupported material was omitted, resulting in public
status `partial` with `quality_audit_warning`, despite full source coverage.
The last run retained thirteen validated product facts and recorded two
`sibling_model` item rejections. It contains no compared-product identity fact.

The third run's local monitoring client briefly lost its HTTP connection.
Persisted task and publication records confirmed completion; subsequent report
API, frontend report page, and readiness requests all returned HTTP 200. No
additional run was submitted. All eight main services were healthy afterward.

Active workflow version: **21**. Active Consensus Analyst version: **13**, with
output `SourceBoundBuyingSynthesis`. Active model tasks bind to DeepSeek V4 Flash.
The latest report's browser-panel open request was queued by the app.

## Regression verification

- Backend suite: **220 passed**, **58 isolated-stack tests skipped** locally.
  One existing Starlette/httpx deprecation warning remains.
- Ruff passed for affected backend files and replay scripts; mypy passed for
  five affected contract/evaluation modules; `git diff --check` passed.
- Tests cover source ownership, unknown references, internal labels, legacy
  snapshot adapters, independent comparison-detail rejection, and failed
  semantic audit blocking.
- Updated strict correction and source-coverage gates each passed **20 matched
  offline cases**, including five repair cases and all paid-stage estimates.
  Correction replay: baseline mean 73,212.35 estimated tokens, candidate
  56,688.75; baseline p95 local time 151.70 ms, candidate 124.95 ms. Source
  coverage replay: baseline mean 55,926.85, candidate 43,573.50; baseline p95
  local time 155.62 ms, candidate 123.88 ms.
- Context atlas audit passed for all 28 notes, links, maps, and boundaries.

These three live results demonstrate publication for this product with warm
caption availability. They do not establish general production token/latency
parity or guarantee cold caption availability. The larger paired gates remain
offline estimates and local fixture timings. The full isolated stack gate was
not repeated during this correction; local tests and the three live public
workflow executions provide the verification recorded above.
