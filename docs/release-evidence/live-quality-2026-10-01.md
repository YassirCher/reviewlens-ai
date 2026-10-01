# Live analysis quality report — 2026-10-01

## Verdict

**Live acceptance failed.** Two fresh Blackshark T11 analyses were run with real DeepSeek V4 Flash responses. The analysis without comments failed publication. The analysis with comments published a partial report, but inspection found an incorrect evidence omission, incomplete citations, and missing product details.

The published report is usable for some cited observations; it does not establish that the application is working correctly. Service readiness and HTTP success do not establish analysis quality.

**Application code was not changed for these tests.** SHA-256 comparisons matched all 212 discovered application, test, and script files before and after testing, with no added files in those sets. Existing uncommitted changes were preserved. Active bindings and the inspected deployed source hashes matched the pre-test configuration. No workflow, model policy, prompt, budget, or completed historical run was modified.

## Test setup and actual results

Exactly two new analyses were admitted through `/api/v2/analyses`, using five videos, English locale, and `deepseek/deepseek-v4-flash` through the real OpenRouter endpoint. Blackshark T11 was used twice to reproduce the previously failing product and compare the comments toggle. No additional paid analysis was launched after the failure.

| Metric | Without comments | With comments |
|---|---:|---:|
| Run ID | `9d527c68-4ad6-4ccb-908b-a8a1ca12fba3` | `7c5793ac-0bba-4c9d-a61d-50b052477f80` |
| Created, UTC | 19:22:30 | 19:24:34 |
| Final run status | **Failed** | **Partial, published** |
| Videos analyzed | 5/5 | 5/5 |
| Independently rechecked stored review quotations | 33/33 valid | 40/40 valid |
| Actual chat calls, including retries | 10 | 13 |
| Recorded total tokens | 63,231 | 67,367 |
| Creation to terminal completion | 74.39 seconds | 72.92 seconds |
| Paid audit calls | 2 | 1 |
| Paid correction / re-audit calls | 0 / 0 | 0 / 0 |
| Final published findings | None | 11: five strengths, six drawbacks |
| Product facts accepted / proposed | 10/31, unpublished | 0/33 |
| Audience analyses | Disabled | Four; fifth source had no available comments |

Total live work: **23 chat calls and 130,598 recorded tokens**. Usage was reported as settled in the public run responses. Calls include every recorded chat operation for these run IDs, including the failed audit retry.

Call breakdown:

- Without comments: planning 1, source curation 1, review extraction 5, synthesis 1, audit 2.
- With comments: planning 1, source curation 1, review extraction 5, audience extraction 4, synthesis 1, audit 1.
- Knowledge projection and product projection made zero model calls. Successful terminal correction/re-audit tasks in the published run used deterministic shortcuts; they are not additional model calls.

Actual active bindings: workflow **30**, review extraction **16** (`ClassifiedVideoExtraction`), synthesis/correction **20** (`DistinctBuyingSynthesis`), auditor/re-auditor **19** (`ReferencedAuditResult`), audience extraction **8**. Both runs used the same configuration; the five selected videos were identical, although their slot order changed.

These are warm-caption tests. Stored caption provenance retains original fetch times from September 29–30 and October 1 before these runs. Each run created different transcript node IDs with matching original caption times. No acquisition tool failed or reported an IP block. This verifies reuse and fresh lineage for these sources; it does not test cold acquisition availability.

## 1. Failure without comments

### Confirmed failure chain

All five review tasks and synthesis succeeded. The first and second `audit_report` attempts failed validation with:

```text
audit_decisions_invalid
finding_checks: audit decision cites unknown, foreign, or duplicate evidence
```

Publication then failed with `report_audit_missing`. There is no public report or publication record for this run. The publication gate correctly withheld a report without a valid audit.

The malformed audit never became a valid semantic rejection, so the bounded correction/re-audit route did not make paid calls. This is an audit contract reliability failure, not missing video coverage or a failed caption fetch.

### Worker log corroboration

Docker API, worker, and scheduler logs were searched and correlated using persisted Celery task IDs. There were 118 matched worker receipt lines across both runs. Important receipts:

| UTC | Celery task ID | Meaning |
|---|---|---|
| 19:23:35.880 | `3a5aee4c-50b2-4142-876e-b96ffe5269cf` | Audit attempt 1 returned application status `retrying` |
| 19:23:44.917 | `5656f935-b800-421c-b9d2-28afbad052d6` | Audit attempt 2 returned application status `failed` |
| 19:23:45.012 | `7a2b97e1-3041-4885-a4c9-d0ba17083d2f` | Publication returned application status `failed` |
| 19:25:41.740 | `f54fa437-c4af-4605-8d13-561b33eccf67` | Comments-enabled audit returned application status `succeeded` |
| 19:25:47.099 | `19a4f8d3-e909-4bab-b174-ddd20fb03071` | Comments-enabled publication returned application status `succeeded` |

Celery prints “Task … succeeded” when its Python wrapper returns. The embedded application receipt can still say `failed` or `retrying`. The task/attempt ledger supplies the specific validator and publication error codes; the content-free worker receipts corroborate execution and timing.

Both rejected audit output hashes were retained:

- Attempt 1: `a12eb3fa317485484f0d36a76063afc08f297fa13388fa7d914064cce29e856e`
- Attempt 2: `bfee323b35fed322c011fa29f9a4b168c961683252a0d91bb5eb15a9fe15647a`

**Diagnostic limit:** the invalid outputs were not retained. The saved combined error does not identify the finding, evidence label, or whether the fault was unknown ownership, foreign ownership, or duplication. The precise offending reference and the model's semantic decisions cannot be reconstructed from these records.

### Code path responsible

`backend/app/analysis/audit.py:217` rejects duplicate references and references outside the individual finding's citation set. `backend/app/analysis/audit_parts.py:173` builds a response schema that bounds finding count and allowed paths but does not enumerate each finding's owned evidence references or require uniqueness for that reference list. Invalid model references therefore pass the response shape and fail the subsequent binding check. The diagnostic collapses several causes into one message.

### Unpublished draft quality

The failed run's synthesis also had defects that would require semantic review even after fixing reference formatting:

- A gaming assertion used the T9 measurements of approximately 425/143 ms for a T11 report, while the same source separately supplied T11 measurements of 424/142 ms. Both models' quotations were attached to the assertion.
- A connectivity assertion combined dual-device support with Bluetooth 5.3, but its attached quotations did not cite Bluetooth 5.3. A separate accepted product fact from that source did contain the version; it was not bound to the assertion.
- Several compound assertions attached large sets of unrelated quotations. A comfort assertion included lightweight/snug-fit language that its assigned citation set did not establish.
- Two drawback entries covered the same app-support topic.

These findings remained unpublished. Valid quotation text and ownership do not prove that every clause of a synthesized finding is supported.

## 2. Published report with comments

[Open the actual published report](http://localhost:3000/r/mBowB3aPfbLu9CiLfwxA-ECtzMFWJZKwHVAYho2bR7I).

The report, page, PDF, and graph endpoints returned HTTP 200. The PDF began with a valid `%PDF-` header. These checks establish transport availability, not visual rendering or semantic quality.

The public score is **76**, confidence **73**, and verdict `buy_with_caveats`. Those are application outputs, not independently measured quality scores.

### Why the yellow warning appeared

The model auditor marked all twelve proposed findings supported. The final audit was `pass_with_warnings` after two omissions:

| Original path | Recorded reason | Independent assessment |
|---|---|---|
| `report_draft.consensus_pros[4]` | `finding_support_mismatch`, deterministic `quantity_or_unit_not_cited` | **Incorrect unit-alias rejection** of the battery/case claim |
| `report_draft.disagreements[0]` | `unsupported_disagreement`, model-selected rejection part `p15` | Cautious omission is reasonable: the two bass descriptions do not clearly establish an opposing conclusion. The saved explanation ends mid-sentence, limiting inspection. |

The full-coverage warning is accurate about analyzing all five sources. It should not be interpreted as proof that every omission was necessary.

### Confirmed false battery omission

The proposed assertion combined up to 30 hours of total battery life with a 500 mAh case. Its citation explicitly supplied both quantities, spelling the capacity as **“500 milliamp hours.”** The stored transcript also contains that wording.

Read-only reproduction using the running application's `statement_mismatches` returned:

| Citation variation | Result |
|---|---|
| Actual captured spelling: `500 milliamp hours` | `quantity_or_unit_not_cited` |
| Same citation with equivalent spelling: `500 mAh` | No mismatch |
| Same citation with incorrect unit: `500 g` | `quantity_or_unit_not_cited` |

`backend/app/analysis/quantities.py:12` recognizes `mAh` and `Ah`, but not this spelled-out capacity unit. This is a confirmed false negative. The battery figure should still be qualified as a **source-stated claim, not an independently measured endurance result**; that limitation was preserved in the report.

### Finding-by-finding quality inspection

| Published topic | Assessment |
|---|---|
| Build quality | Attached excerpt supports “solid.” “Sturdy” is present at a different stored passage around 99.9 seconds, but that passage is not attached. Citation completeness gap. |
| Slide-cover design and opening sound | Attached excerpt supports the slide-cover design. The opening sound appears in an adjacent stored passage, not in the attached excerpt. The compound assertion needs both passages or narrower wording. |
| Weight | Nearby caption context confirms the 3.2 g figure refers to each earbud. The short attached excerpt omits that scope; the context resolves it. |
| Fit | Stored context qualifies the fit as applying to “most users.” The finding and shortened excerpt drop this qualifier and present fit too broadly. |
| Gaming latency | Correctly retains the source-specific T11 result of 142 ms with gaming mode on. This is a reviewer test result, not a universal latency guarantee. |
| Companion app | Directly supported by the attached quotation. |
| Sound leakage | Directly supported, with the high-volume condition retained. |
| Outside noise | Relevant quotation is attached, but translated wording is awkward about in-ear versus semi-in-ear design. Original-language verification remains outstanding. |
| Volume controls | Retains the narrower observation that the reviewer did not find the option. It should not be generalized into proof that every volume-control method is absent. |
| App-support limitation | Attributed quotation is retained, but substantially repeats the companion-app drawback. |
| Durability concern | Reviewer concern/speculation is preserved with attribution. It is not proof of an observed long-term failure. The entire Bengali quotation is exposed in the English report, impairing readability. |

The model auditor accepted the compound build/design findings despite their incomplete attached support. A valid audit response contract did not establish clause-level audit accuracy in this case.

### Report completeness and presentation

- Product details are absent: `product_info` is null. All five product projection tasks succeeded, but **all 33 proposed facts and all three variants were rejected**. Fact diagnostics show 29 `scope_not_supported` and four `value_not_supported` rejections; eight of eighteen sample details survived.
- The model's rejected product scopes/values are not retained in the postprocessed task output. The counts and literal scope-check code are inspectable, but these records do not establish that all 29 scope rejections were false. Valid facts such as 3.2 g and Bluetooth 5.3 survived in the other run, demonstrating unstable product-fact retention across the same source set.
- `who_should_buy` and `who_should_avoid` are empty. The separate decision guide still supplies three strength pointers, three caveat pointers, source pointers, and unknowns; buyer guidance is therefore limited, not wholly absent.
- The safe summary became a mechanical selection of one strength and one drawback after pruning. Sound quality/bass observations no longer receive a top-level finding despite stored evidence, leaving an important earbud buying topic underrepresented.
- Internal topic names such as `build_quality` and `software_support` appear in user-facing prose. Two of six drawbacks concern app support, using scarce report space for an overlapping topic.
- No unsupported long-term ownership period was published; the longest-use fields are null.

### What the comments branch actually contributed

Four audience model calls processed **83 sampled comments and 61 retained comments**. CAM Compares had unavailable comments and was skipped without a paid audience call. One analyzed source had only one retained comment and an audience confidence of 10; it cannot establish recurrence.

The other audience outputs described charging, one-sided audio, pairing, and static complaints. These are audience signals, not independently verified product defects. Their recurrence and sentiment labels were not independently adjudicated in this review.

All four `audience_agrees_with_reviewer` values were null, and the report's audience score adjustment was **0**. The public report response has no dedicated audience-analysis section. Most audience-specific complaints are not visible in the top-level findings. Comments were processed, but their visible contribution is limited.

One audience limitation incorrectly called **2026-07-11** a future date, despite the run occurring on **2026-10-01**. That is a confirmed temporal reasoning error in the internal audience output.

## 3. Evidence and storage checks

All 73 distinct stored review quotations across the two runs passed the existing source ownership, `DERIVED_FROM` lineage, transcript word-sequence, bounded-span, and timestamp checks when re-executed against their saved source material. This is a deterministic integrity check, not an independent semantic-accuracy percentage or verification of the reviewer's real-world measurements.

All saved context bodies were readable with their integrity checks: 99 node versions in the failed run and 116 in the published run. No missing or corrupt Markdown body was found in either test. Graph/vector projection state was pending at capture; pending alone was not classified as a projection failure. The public graph endpoint was available.

Five distinct channels and five unique videos were selected; their durations were 185–571 seconds, meeting the configured three-minute minimum. Spanish and Turkish captions were delivered as English translations; Bengali captions remained Bengali. Translation/caption accuracy was not independently verified against audio.

## 4. Recommended next corrections — not implemented

The user requested testing and reporting without application changes. The following are recommendations only:

1. **Make audit reference output reliable within the existing call budget.** Bind references from the finding in code where the model does not need to select them, or constrain each indexed decision to its owned reference set. Normalize repeated valid owned references safely; continue rejecting unknown/foreign evidence. Record the exact decision path, reference category, and ownership result, and supply concrete retry targets.
2. **Recognize equivalent typed units.** Support explicit spoken capacity aliases such as milliamp hours while retaining dimension checks and rejection of grams as battery capacity. Keep endurance claims qualified as claimed unless the citation establishes a test.
3. **Enforce complete atomic citation support.** Split compound clauses or attach their distinct supporting passages. Preserve source-specific model identity, test conditions, and qualifiers such as “most users.” Evaluate the auditor on these captured cases rather than supplying expected model decisions.
4. **Investigate product scope attrition with bounded diagnostics.** Distinguish requested product identity from variant constraints, preserve sibling-model protection, and retain enough sanitized per-item scope/value metadata to explain rejections. Do not accept all rejected details without evidence.
5. **Improve the surviving report without adding calls.** Preserve supported buying topics and meaningful drawbacks, deduplicate overlapping app topics, render readable labels, provide English attributed summaries alongside original quotations, and preserve supported buyer guidance when unrelated findings are pruned.
6. **Validate audience output deterministically where possible.** Compare dates to the run date; expose cautious audience summaries with actual sample counts. Avoid presenting one comment or questions as repeated verified faults.

Acceptance should require actual fresh reports to publish with valid findings and material drawbacks retained. A fix to audit formatting alone will not resolve the observed citation and product-retention problems.

## 5. Scope and retained evidence

This report is based on actual model usage, persisted task/attempt validators and audit decisions, correlated worker receipts, public report payloads, source provenance, and bounded transcript checks. No fixture supplied the audit outcomes for these two analyses. No application-code edits or extra paid retry runs were made to turn the observed failure into a pass.

The ignored `.audit-cache` contains the two admission/result files, two task diagnostic captures, two evidence-check captures, the source hash baseline, final check results, and 118 correlated content-free worker receipts. Report prose excludes secrets, raw prompts, full transcripts, and individual commenter bodies or authors. Existing user-owned `live-examples-2026-10-01.md` and historical run records were preserved.

Two samples do not establish general reliability, production token parity, or p95 latency. The faster comments-enabled sample is confounded by the other run's extra audit call, concurrency, source-slot ordering, and model variability. The evidence does not establish that enabling comments fixes the audit failure.

**Final assessment: source coverage and publication blocking worked in these cases; audit reliability and report quality remain unresolved.**
