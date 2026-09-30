# Two-product audit repair and capped live verification — 2026-09-30

## Original run diagnosis

The authoritative task attempts, usage records, audit payloads, bounded cited excerpts, and worker logs were inspected for both user-submitted runs. Completed records and configuration snapshots remain unchanged.

| Product | Original run | Result | Sources | Chat calls | Recorded tokens | Completion |
|---|---|---|---:|---:|---:|---:|
| Blackshark T11 | `7dde0e0d-2427-4edf-8e3c-181c356d5cd3` | Published with evidence warnings | 5/5 | 9 | 53,351 | 68.11 s |
| HP Omen 16 Max, comments enabled | `4b5d4bea-1030-4289-8b6c-1617dbcc0d2e` | Failed at publication | 5/5 | 14 | 128,713 | 79.08 s |

Both runs acquired and analyzed all five reviews. Caption acquisition was not their publication blocker. Blackshark lost an unsupported battery finding and an unverified usage-duration claim. The HP model auditor rejected every finding and returned uppercase known issue codes. The grounding handler compared codes case-sensitively, treated them as unknown failures, and skipped the eligible correction and re-audit. `publish_report` correctly blocked the failed audit.

Source claim prose also contained details absent from its quoted excerpts. Synthesis repeated compound claims and uncited prices, configurations, or conditions. Valid HP findings such as upgradable RAM and the productivity battery benchmark were rejected alongside genuinely unsupported material. Raw model responses were not retained, so the auditor's internal reasoning cannot be reconstructed.

## Corrections

- Canonicalize only recognized grounding issue codes by case. Unknown failures and missing validated central evidence remain blockers. The existing maximum of one correction call and one re-audit call is unchanged.
- Omit unverified usage-period prose from model-facing source metadata.
- Publish `QuoteSynthesisInput`: server-bound short references and verified original excerpts without broader derived claim prose. Keep assertions bound to one source and reject unknown or mixed references.
- Publish `CitedAuditorInput`: each finding includes its own original cited excerpts and source ownership at its original report index. Disagreement sides retain their owners' verified quotation sets. An absent extra retrieval packet is no longer rendered as absence of authorized evidence for these contracts.
- Preserve compiled adapters for the previous synthesis and both full-analysis and catalog auditor inputs. Public report shapes, scoring, caching, optional product validation, and publication gates remain intact.
- Compare explicit mixed fractions exactly: `7 and 1/2` equals `7.5`. Component digits are not treated as separate measurements. Wrong values and foreign-source support still fail. Semantic audit remains responsible for meaning, units, conditions, attribution, and scope.

Normal model calls remain nine for five reviews, or fourteen with five audience analyses. The default system-prompt estimate is 2,513 tokens, below the existing 2,515-token gate.

## Live tests: exactly three fresh runs

Every recorded chat call used `deepseek/deepseek-v4-flash` through OpenRouter; usage records identify Alibaba as the selected provider. Tests used public admission and status endpoints, fresh immutable snapshots, five requested sources, and the user's original comment settings. Admission was never automatically retried.

| Test | Run | Workflow | Result | Sources | Calls | Tokens | Completion | Retained findings / product facts |
|---|---|---:|---|---:|---:|---:|---:|---:|
| Blackshark T11 | `b149ee88-8c01-41df-9d5a-9ab10ef2cf0b` | 22 | Published, `pass_with_warnings` | 5/5 | 9 | 50,082 | 68.39 s | 9 / 16 |
| HP with comments, initial repair | `cd1ef52e-46b0-43df-a03e-cc0dd7f70b48` | 22 | Failed, unpublished | 5/5 | 16 | 143,206 | 102.11 s | No report |
| HP with comments, quote-only synthesis and inline audit | `b3845388-1603-4530-9684-b4a393f7daca` | 23 | Published, `pass_with_warnings` | 5/5 | 14 | 118,690 | 75.01 s | 11 / 12 |

The intermediate HP test confirmed actual correction and re-audit calls, one each. Both audits still rejected all findings, and publication remained blocked. This led to the quote-only synthesis and direct-citation audit correction before the final test. The final HP semantic audit accepted the findings; deterministic comparison omitted one battery finding because `7.5` did not match the caption spelling `7 and 1/2`.

The exact mixed-fraction fix was applied after that final paid test and verified with regression tests and a read-only replay of its stored draft and source evidence under the accepted semantic audit. That deterministic replay retained all twelve findings and passed. No fourth paid analysis was launched. The completed paid report remains unchanged with its original warning and eleven findings.

Published frontend reports:

- [Blackshark T11](http://localhost:3000/r/zpoKnD1m6kyydPkLjVruIc6bEVOuI4cLKyzaxcJ2WCM)
- [HP Omen 16 Max](http://localhost:3000/r/yu_i29CkZHhKB2ZArwp07WcN34stELGOnVreqlOv6-k)

Both published runs have full source coverage. Their `partial` status denotes evidence warnings and omitted findings, not missing videos or an execution failure. The successful HP run used fewer tokens and less elapsed time than the original failed run; Blackshark used fewer tokens and took 0.29 seconds longer. These small live observations do not establish production token or latency parity.

## Regression and acceptance evidence

- Final local backend suite: **230 passed, 58 stack-only tests skipped**. Backend Ruff and six-module Mypy checks passed. Context audit passed for 28 notes; whitespace checks passed.
- Isolated full backend stack with the new input contracts: **283 passed, four host-only gates skipped**. This preceded the final mixed-fraction regression; that narrow change passed the local suite and the captured live-input replay.
- Six actual worker fixture workflows passed: normal complete (9 calls), comments (14), lowercase and uppercase recoverable audits (11 each, exactly one correction and one re-audit), failed re-audit (11, unpublished), and malformed correction (10, no re-audit, unpublished).
- Workers recorded zero curator model calls and zero separate product-information model calls. Context isolation, graph projection idempotency, and transactional rollback checks passed. Fixture drills made zero paid calls.
- Added regressions cover quote-only input, original-index preservation, inline citation ownership, disagreement evidence, unknown references, legacy snapshots, unverified duration, uppercase pruning, explicit fraction equivalence, wrong values, and foreign-source support. Existing failed-audit gates remain covered.

Two independent twenty-case offline paired gates passed all strict evidence, source/topic coverage, call-count, token, and local p95 timing checks. Five cases in each gate include correction and re-audit. The correction gate's baseline explicitly keeps its captured full-analysis auditor contract; it cannot silently inherit the successor input.

| Offline measure | Captured correction baseline | Successor | Coverage baseline | Successor |
|---|---:|---:|---:|---:|
| Mean estimated total tokens | 73,212.35 | 48,443.75 | 55,926.85 | 37,823.50 |
| p95 estimated total tokens | 94,204 | 56,417 | 63,736 | 42,487 |
| p95 local completion | 154.75 ms | 131.72 ms | 166.32 ms | 133.35 ms |

These replays use captured common-call usage, changed prompt/schema/completion estimates, and fixed 2 ms fixture-provider delays. They exclude remote inference and production queue/network time. They establish deterministic regression bounds, not production parity or a general semantic-model evaluation.

## Rollout and host recovery

The laptop reboot left stale Docker IPC sockets and prevented Docker Desktop from starting. Only verified socket-only temporary directories were renamed to preserved backups before restart. Project volumes, settings, and database records were preserved; no factory reset was performed. Isolated acceptance ran sequentially with the main app stopped to respect the laptop's 4 GB Docker memory limit. Only the isolated test project's disposable volumes were removed.

Rebuilt and restarted API, worker, scheduler, and migration images. Active workflow **23** binds Consensus Analyst **15** (`QuoteSynthesisInput` → `SourceBoundBuyingSynthesis`) and Quality Auditor **14** (`CitedAuditorInput` → `AuditResult`); all eight model selections remain DeepSeek V4 Flash. Existing reports and failed runs are immutable. All eight services are healthy; API readiness confirms five dependencies, both public report endpoints return HTTP 200, and the HP frontend report returns HTTP 200. The application is restored using its original volumes.
