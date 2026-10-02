# Evidence binding and audit reliability — 2026-10-02

Baseline: `eda2ada`. This change includes the implementation, regressions and verification evidence. The final build is deployed locally; live quality acceptance remains unresolved.

## Result

**Both authorized paid runs published, but neither passed the agreed quality acceptance criteria.** The no-comments run retained all five reviews but lost product facts and included an incorrectly attributed comparison. The comments run retained four reviews and had failed audience tasks. Subsequent corrections are verified locally; the final code has no clean live acceptance. Both paid admissions are exhausted. No third analysis was launched, and both completed records remain unchanged.

## Implemented corrections

- Extraction selects server-generated caption references. Code supplies verbatim quotations, timestamps, assigned source identity and fresh evidence lineage. Each claim has at most two references; each video has at most six claims. Catalogs use only the existing selected context, in chronological order, with spans bounded to 300 characters and 45 seconds.
- Audit decisions are exhaustive and keyed by the original finding paths. Citations and allowable rejection parts are code-owned. Missing, duplicate or foreign decisions/references fail validation; retries receive indexed diagnostics and an output hash. Rejected findings still require an explanation. Malformed audits and failed final audits remain unpublished.
- Product validation treats capacity aliases and amp-hour conversions consistently, separates identity from additional regional/variant qualifiers, and checks sibling-model passages independently. Optional product rejection does not discard a valid review. Bounded item diagnostics identify the path, proposed value/scope, references and rejection code.
- Ownership duration requires an explicitly cited passage about using the requested device. Runtime, a child's age and ownership of another device do not qualify. The 30-day threshold and scoring coefficients are preserved.
- Findings retain attributed English claims with original citations, readable topic labels and distinct material drawbacks. Summary and buyer guidance use fixed templates over the surviving audited findings. Unrelated guidance survives pruning. Repeated topic aliases are conservatively deduplicated; opposing observations remain separate.
- Audience recurrence requires at least two distinct retained comment references. Future dates are excluded relative to the run timestamp; sample counts are code-owned. Only retained review sources contribute to report-level audience aggregation. Comments remain secondary signals.
- Legacy review, synthesis, audience, curator and audit snapshots retain compiled adapters. Public report/API fields, caption caching, normal call allowances and bounded repair allowances are preserved.

## Additional defects discovered during live verification

1. **Generic product scope:** the model supplied `product`, `model` or `T11` as the optional scope. These identity-only labels were being treated as uncited variant qualifiers. The validator now accepts identity-only scope when assigned-source metadata establishes the requested product, while keeping checks for additional qualifiers and sibling passages. The successor prompt requests `null` when there is no cited qualifier.
2. **Comparison attribution across captions:** a T9 latency number preceded the next caption's `The T11` transition. Treating that combined quotation as wholly about T11 allowed an incorrect number. Catalog construction now tracks explicit model ownership across contiguous selected captions and excludes foreign subject portions before exposing references.
3. **Bengali duration units:** a cited Bengali 25–30-hour passage was incorrectly pruned. Validation now normalizes captured Hindi/Bengali duration unit words without translating or changing the stored quotations.
4. **Markdown reconciliation race:** reconciliation could move a just-written file before its database transaction committed. Six quotation files in the first fresh run ended up in quarantine. Node creation/versioning and reconciliation now share a workspace transaction lock. Publication verifies active source/evidence nodes and readable, matching quotation bodies before writing a new report. Genuine missing or corrupt bodies still block publication.
5. **Over-filtered caption catalog:** the fifth video's title identifies T11, while the selected captions contain `T1` and `T111`. The comparison filter incorrectly discarded every selected passage. Filtering now requires both requested and foreign model mentions before carrying comparison ownership across passages. Neutral passages remain available; explicit sibling facts are still checked independently. The discrepancy's cause is unknown, and these model codes are not silently normalized into T11.
6. **Truncated structured responses:** three requests in the comments run ended with `chat_content_truncated`. The rejected payloads were not retained, so their exact content is unknown. Successor review and audience schemas now bound optional facts, prose and signal counts more tightly within the existing completion ceilings. Reasoning was already disabled; no new call, token ceiling or budget was added. This reduces output size but cannot guarantee future completions.
7. **Audience validation retry:** invalid sentiment totals previously produced an unhelpful root validation error. The retry now receives the three field names, actual total, required total of 100 and output hash. Invalid totals still fail validation rather than being silently converted into valid analyses.
8. **Lost accounting on invalid responses:** paid HTTP-200 completions that could not be decoded released their reservation without recording returned usage. Those failures now retain actual model/generation metadata and usage, consume the reported budget, and remain failed ledger events. No raw completion content is retained. The three historical truncated requests cannot be repaired from their hashes; the comments run's recorded token total is a lower bound.
9. **Bengali driver units:** the captured `13 এমএম` driver passage now matches `13 mm` during validation. Stored quotation text is preserved. Ambiguous capacity wording such as `এমএচ` is not guessed into a battery unit.

## Verification

| Check | Result | Meaning |
|---|---:|---|
| Backend unit/API regressions | 391 passed | Includes reference ownership, malformed audit decisions, quantities, scope, usage, audience checks, guidance and publication storage gates |
| Real PostgreSQL/Neo4j/Qdrant/public/admin integration | 22 passed | Includes concurrent uncommitted file creation versus reconciliation, rollback/orphan handling, immutable records, invalid paid-response accounting and public contracts |
| Worker fixture scenarios | 7 passed | Valid reports publish; failed final audits, empty corrections and unchanged corrections remain unpublished |
| Legacy workflow worker replay | Passed | Five retained sources, nine model requests to local fixtures, published report |
| Browser checks | 2 passed | Public fixture report and evidence-map journey, and no serious/critical accessibility violations on the root page |
| Ruff | Passed | Backend application and tests |
| Mypy | Passed | Changed admin evaluation and LLM gateway paths |
| Context audit | Passed | 28 Target V2 notes, links and code maps |
| Historical immutability | 4/4 unchanged | Saved completed-run and workflow-snapshot hashes match after rollout; both new paid reports, records and snapshots also match before/after final restart |

Worker measurements are actual local fixture requests: complete **9**, comments **14**, recoverable audit correction **11**, failed re-audit **11**, empty correction **10**, unchanged correction **10**, case-normalized correction **11**. Product projection and knowledge projection make **zero** model calls. Empty/unchanged corrections make no re-audit call. All of these checks use mocked provider decisions and do **not** establish live semantic audit accuracy.

Earlier integration attempts exposed an invalid new race fixture and reused idempotency keys in two existing tests; those fixtures were corrected. The final integration rerun initially used an outdated mock-provider container that lacked the compact successor contracts. Its deployed hash differed from the local fixture; after rebuilding that mock, all 22 checks passed. Gateway integration tests also activate their own fixture workflow, so the isolated analysis binding was restored before worker checks. The application was not altered to accommodate those fixture failures.

The browser fixture earlier exhausted the isolated stack's simulated search quota (100/100). After verifying both upstreams were local mocks and there were no active reservations, only that mock usage counter was reset. Configured limits and the real application's counters were unchanged. Both final browser checks passed. Failed fixture attempts remain recorded separately.

### Matched offline replays

Twenty-four distinct, independently labelled response cases compare quantity/subject rejection and fact retention against `eda2ada`, using captured source inputs and synthetic responses. Reconstructed missing historical outputs are not claimed to be recovered model responses.

| Measurement | Baseline | Candidate |
|---|---:|---:|
| Supported assertion retention | 8 | 12 |
| Supported product-fact retention | 7 | 12 |
| Unsupported assertion retention | 2 | 0 |
| Estimated total tokens | 821,336 | 728,349 |
| Local p95 processing time | 56.4 ms | 85.9 ms |

Estimated tokens are **11.3% lower**. The accounting includes changed prompts, schemas, selected context, synthetic outputs, all replayed retries and bounded correction/re-audit work. Two unchanged planning calls are held equal. Captured source inputs are held equal; topic retention is measured for each labelled assertion. This is not generated whole-report quality evaluation. Local processing time increased and is reported separately from provider latency or production application completion time. **Production token/latency parity is unverified.**

## Deployment

The final running deployment uses workflow **33**, published after the second paid run:

| Binding | Agent version | Contract |
|---|---:|---|
| Review extraction | 19 | `SpanReviewInput` → `CompactSpanVideoExtraction` |
| Audience extraction | 10 | `CompactAudienceDraft` |
| Synthesis/correction | 21 | `CompleteBuyingSynthesis` |
| Audit/re-audit | 20 | `OwnedAuditResult` |

API, worker, scheduler, frontend and their required dependencies restarted successfully. Readiness returned HTTP 200. Every changed application file's local SHA-256 matched the deployed API, worker and scheduler copies. Active model bindings use `deepseek/deepseek-v4-flash` and the actual OpenRouter endpoint. Readiness is an infrastructure check; it does not certify report quality. Workflow 33 has only local fixture verification; no additional paid analysis was admitted after the two-run cap was reached.

Docker also needed recovery after the laptop restart because its stale IPC sockets prevented startup. Only verified socket directories were relocated to recoverable backups. Earlier Docker VM logs recorded an OOM kill with the global WSL cap at 4 GB; that cap was backed up and increased to 8 GB. Project volumes and completed analyses were retained. This host resource change does not change model budgets.

## Exactly two fresh live analyses

Both requested five videos, English, and DeepSeek V4 Flash. The first used workflow 31; the second used workflow 32 after the first run's attribution, scope, duration and storage defects were corrected. Every decoded successful chat recorded the requested Flash model. Three truncated calls in the second run lost actual-route metadata under the accounting defect described above; their configured binding was Flash, but their actual route is unavailable.

| Measurement | Without comments | With comments |
|---|---:|---:|
| Run ID | `5ae6b745-f81d-4ce4-8786-dd6f546c21d2` | `860daa40-c5ab-48c5-be5d-d39dc935ee2a` |
| Publication | Partial, evidence warnings | Partial, evidence warnings |
| Validated reviews | 5/5 | 4/5 |
| Actual chat request attempts | 9 | 16 |
| Recorded tokens | 46,430 | **59,376, lower bound** |
| Creation to completion | 65.3 s | 230.1 s |
| Product facts retained/proposed | 0/36 | 3/8 |
| Final buying findings | 10: six strengths, four drawbacks | 10: seven strengths, three drawbacks |
| Correction/re-audit calls | 0/0 | 0/0 |
| Stored quotations revalidating after completion | 51/57 | 46/46 |
| Stored node-version bodies readable | 123/129 | 117/117 |
| Quality acceptance | **Failed** | **Failed** |

**Without comments:** a T9 latency result was incorrectly attributed to T11. Six quotation bodies were quarantined by the reconciliation race. Product scopes were over-rejected, leaving no product facts. A valid Bengali duration was also pruned; a separately omitted T9 runtime claim lacked its supporting duration citation. The completed report is preserved, including its defects.

**With comments:** the fifth review failed before a model request with `caption_span_catalog_empty`. The review model for another source truncated once, then succeeded on its allowed validation retry. Two audience tasks exhausted their attempts after truncated output or invalid percentage totals; another audience task retried successfully but was excluded from the report because its review was not retained. A source with zero retained comments required no audience model call. There were four validation retries, no paid repair/re-audit calls, and no increased allowance.

The second run retained cited earbud weight (3.2 g), dimensions (1.86 × 3.2 cm) and charging-case capacity (500 milliamp hours), together with controls and gaming-latency drawbacks. Its Bengali 25–30-hour passage and cited usage condition survived. The T11 latency attribution and all quotation files passed the post-first-run fixes. The driver-unit omission, catalog failure, audience diagnostics and invalid-response accounting were corrected after this second paid run and tested locally.

Both model audits marked all twelve original findings supported. Deterministic validation still pruned two insufficiently cited findings in each run. In the second run, the stable-connection citation did not establish Bluetooth 5.3, and the bass quotation did not establish a 13 mm driver. These are legitimate omissions; exhaustive audit decisions do not prove that the model's semantic judgments are accurate.

The second report's HTML, PDF and evidence graph returned HTTP 200, and the PDF had a valid PDF header. That confirms delivery, not quality acceptance. Completed payloads and workflow snapshots are not rewritten by the later fixes.

## Limits

- Both paid admissions are exhausted; no third paid analysis was launched.
- The first fresh completed report remains unchanged, including its quality defects. Historical missing bodies are not recreated from hashes or guessed text.
- No raw model-response retention was enabled. Historical unretained Hindi quotation defects remain unknown.
- Cached caption availability does not guarantee cold-run YouTube availability.
- Genuine evidence omissions may still produce the existing yellow warning. Missing central evidence and failed final audits still block publication.
- Two samples cannot guarantee future model judgments or production performance parity. The paired replay is a deterministic validator/cost check, not live-model accuracy evidence.

## Reproducible evidence

Tracked regressions: `backend/tests/test_evidence_binding.py`, `backend/tests/fixtures/live_binding_cases.json`, and `scripts/check_evidence_binding.py`. The concurrency regression is in `backend/tests/integration/test_phase4_context_stack.py`.

The tracked machine-readable summary is `docs/release-evidence/evidence-binding-checks-2026-10-02.json`. Local bounded verification artifacts are retained under `.audit-cache/`: `evidence-binding-replays.json`, `binding-worker-results.json`, `binding-integration-post-live.log`, `binding-browser-fixture-final.log`, `binding-legacy-worker-final.log`, `binding-deployment.json`, `binding-immutability-final.json`, `binding-live-immutability-before.json`, `binding-live-immutability-after.json`, and the two `binding-live-20261001-*.json` admission guards. Provider secrets and commenter identities are excluded from release evidence.
