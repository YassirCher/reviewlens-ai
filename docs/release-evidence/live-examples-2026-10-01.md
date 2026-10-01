# Live application verification — 1 October 2026

## Result

**The application is running, but analysis reliability is not acceptable yet.** Two fresh examples were tested against the real OpenRouter service using `deepseek/deepseek-v4-flash`. The example without comments published with warnings; the example with comments failed. Both acquired and analyzed all five requested videos. This is live verification of those outcomes, not a passing release acceptance claim.

No backend or frontend implementation was changed during this investigation. The deployed code matches commit `7a3aa9c`. Exactly two fresh analyses were admitted; no third test, paid repair experiment, or retry of run admission was performed. Normal bounded workflow retries are included below.

## Launch and configuration

Docker Desktop initially failed before its Linux engine became available. Its local backend log reported an inaccessible `Docker/run/dockerInference` socket. Native file deletion and the earlier socket cleanup helper failed with Windows error 1920. The two inspected runtime directories contained only three zero-byte socket files.

With Docker stopped, those runtime directories were preserved under these names, allowing Docker to create fresh endpoints:

- `%LOCALAPPDATA%/Docker/run.reviewlens-stale-20261001144220`
- `%LOCALAPPDATA%/docker-secrets-engine.reviewlens-stale-20261001144220`

Containers, database volumes, settings, and completed run records were preserved. Startup succeeded after this change. Similar inaccessible socket failures and parent-directory recovery are reported in [Docker's issue tracker](https://github.com/docker/for-win/issues/15064). This workaround restored this launch; it does not establish that the Windows/Docker failure cannot recur.

`docker compose up -d --wait --wait-timeout 180` completed successfully. The eight running services are healthy, migration exited successfully, the frontend returned HTTP 200, and `/health/ready` reported PostgreSQL, Redis, Markdown storage, Neo4j, and Qdrant available. No test runs remained active at the final check.

Active workflow: **26**, `4c064915-be91-4dca-b122-06ee580008d1`.

- Consensus Analyst **17**: `CatalogRepairSynthesisInput` → `EvidenceBoundBuyingSynthesis`.
- Quality Auditor **16**: `DecisionAuditorInput` → `FindingAuditResult`.
- All eight executable agent bindings select DeepSeek V4 Flash.
- SHA-256 hashes of audit, grounding, synthesis, prompting, executor, and registry files match between the checkout, running API, and running worker.

## Fresh examples

Both requests used five videos and English locale. They were submitted through the normal public preflight/admission endpoints and monitored to a terminal state.

| Example | Comments | Run ID | Sources | Chat calls | Actual tokens | Admission to completion | Outcome |
|---|---|---|---:|---:|---:|---:|---|
| iPhone 18 Pro Max | Off | `cc4c09a4-7c99-436e-bad3-45619e78344b` | 5/5 | 9 | 70,124 | 75.56 s | Published, `partial`, `pass_with_warnings` |
| Samsung S25 Ultra | On | `5340afe7-5aa4-48f4-81c9-5e2363bb252a` | 5/5 | 14 | 116,148 | 81.82 s | Failed, unpublished |

Every chat usage record identifies `deepseek/deepseek-v4-flash` and has complete usage. No provider transport retry was recorded. The Samsung synthesis validation retry is counted as a second chat call even though its provider request itself succeeded.

### What worked

- Each example selected five distinct videos from five distinct channels. All ten videos exceeded the three-minute minimum; the shortest was 705 seconds.
- All ten transcript tasks and review analyses succeeded. Read-only replay against each run's own stored transcripts verified **35 iPhone quotation links and 51 Samsung quotation links**, with zero quotation/timestamp or source-ownership failures. Source/transcript lineage checks passed.
- Comments off caused zero comment-fetch or audience-analysis calls.
- Comments on executed five successful `youtube.comments` calls and five successful audience analyses: **150 sampled comments, 100 retained**. Only aggregate audience results were exported into diagnostic captures.
- Knowledge curation and separate product-information tasks made **zero model calls** in both examples.
- The published iPhone report, report API, PDF, and graph API returned HTTP 200. The PDF had a valid `%PDF-` header. Browser inspection confirmed the warning, source coverage, findings, and an expandable charging citation linking to the correct video moment.
- The earlier “half an hour” problem did not recur: the published report retained charging from 0 to 81% in 30 minutes, plus full charge in 55 minutes, against the actual caption wording.
- The failed Samsung run has no report publication. The publication gate remained effective.

## Issue 1 — comments example still fails during synthesis

The Samsung review and comment branches completed successfully. The blocker is downstream in `build_consensus`:

| Attempt | Provider usage | Application result | Validator detail |
|---|---:|---|---|
| Primary | 5,782 tokens | `synthesis_reference_invalid` | `catalog_label_in_prose`, location `assertions[0]` |
| Validation correction | 5,839 tokens | `synthesis_reference_invalid` | Same code and location |

Both provider calls returned structured output, but application binding rejected it. In `EvidenceBoundBuyingSynthesis.as_report`, the prose guard rejects a standalone `s1`–`s8` or `e<number>` token in an assertion's attribute, observation, or conditions. The system prompt already instructs the model to keep those labels in reference fields. This run shows that the prompt plus one retry did not reliably enforce that requirement.

The literal rejected token and original assertion are unavailable because raw invalid model responses are not retained. The precise code/path is verified; treating a particular alias as the historical output would be speculation. The successor guard permits `S25`, so this diagnostic does not establish that the product name `S25` was rejected.

No valid draft reached `audit_report`. Audit, report correction, and re-audit were skipped, and `publish_report` failed with `report_audit_missing`. The run-level error is `workflow_task_failed`; the public message intentionally displays the generic analysis failure.

The 14 calls comprise planning + source curation + five reviews + five audience analyses + **two synthesis attempts**. There were **zero auditor, report-correction, or re-audit calls**. The attempt called “correction” in the synthesis trace is its validation retry, not the later `correct_consensus` stage.

**Recommended correction:** resolve known source attribution labels to readable attribution in code after citation ownership is validated. Reject foreign or ambiguous ownership and unknown evidence; harmless presentation labels should not consume a retry or discard an otherwise valid draft. Add bounded diagnostics for the offending prose field/token class and tests for this failure path, without retaining raw responses or allowing real product codes to be mistaken for references.

## Issue 2 — the iPhone warning combines a correct omission and a false rejection

The actual model auditor supplied all twelve finding decisions and marked every finding supported. Deterministic grounding then removed two findings, published nine strengths and one caveat, and recorded `quality_audit_warning`. Correction/re-audit tasks completed through their deterministic skip paths; neither made a model call.

### Correct omission: an incompletely cited comparison

Original `consensus_pros[2]` reported nearly 19.5 hours of active use **and an improvement over last year's 18 hours**. Its only citation stated the current phone's nearly 19½-hour result. The previous-year result is present in another stored quotation, but the assertion did not cite it.

The duration checker correctly found 1,080 minutes unsupported by the supplied citation. The 19½ ↔ 19.5 conversion itself worked. The finding should cite both existing same-source excerpts or be narrowed to the current result. The live auditor's positive decision on the incomplete comparison is an observed semantic accuracy gap.

### False rejection: aperture formatting

Original `consensus_cons[1]` described limited photo-quality benefit when changing the default **`f/1.8`** setting. Its caption quotation explicitly says **`f1.8`** and describes the same limited benefit.

The generic number parser extracts 1.8 from the finding's slash notation but omits it when it immediately follows the letter `f` in the caption. Read-only replay reproduced the mismatch. Changing only the caption notation from `f1.8` to `f/1.8` makes the current checker accept the statement.

**Recommended correction:** normalize explicit aperture notation as an optical quantity before comparison, while retaining protection for product codes such as T11. Keep the existing source-bound evidence requirement. This can be deterministic and adds no model call.

## Additional quality observations

- **Material drawback coverage remains uneven.** The iPhone synthesis used ten strength slots and only two caveat slots before pruning. The final report retains the heavy-use battery caveat and the camera disagreement, but its main caveat findings omit already cited weight/build observations. The source cards still show the heft and less-premium frame. Selection should reserve room for independently supported drawbacks and buyer fit within the existing twelve-assertion limit.
- **Product conflicts are not consistently flagged.** The published product section shows `chipset_name: Apple A20 Pro` and `Processor: A16 Pro`, both with `conflicting=false`, because their property labels/groups differ. This verifies conflicting source-derived values in the same report, not which processor is correct. Equivalent property names need canonicalization and conflict/scope handling. A matched caption alone cannot resolve factual accuracy or transcription errors.
- **Review type conflicts with stated duration.** The UrAvgConsumer source card is labelled `long_term` while its stated usage is “a full day.” Classification needs consistency checks against the actual reported duration; the present output should not imply extended ownership evidence.
- **Optional details are independently rejected as intended.** iPhone product extraction accepted 17 of 22 proposed fact items before merging into 16 public facts; nine proposed sample details were excluded. Samsung accepted 24 of 34 fact items. Recorded rejection categories were `value_not_supported` and `quote_or_timestamp_mismatch`. Invalid optional details did not discard the successful reviews. These counts do not establish that every rejected detail was correctly rejected.

## Separate storage warning

Worker logs repeatedly reported **six Markdown reconciliation failures**. Read-only checks traced them to older runs:

- `21927512-b747-4c56-ac0e-6be83a4b9b54`, created 21 September: one missing transcript body and four missing transcript-chunk bodies.
- `66677476-8812-465c-bf4d-9ace19f86345`, created 24 September: one missing report-node body.

Those workspaces are degraded and affected nodes quarantined. Neither fresh run has missing or mismatched stored node versions: iPhone **115 valid**, Samsung **148 valid**. The historical storage warning is a separate maintenance issue; it is not the cause of these two outcomes. No historical bodies or records were rewritten.

## Evidence and limits

The sanitized captures and read-only replay outputs are in the ignored local `.audit-cache/` directory:

- `live-examples-20261001-{0,1}.json`: admission, terminal status, and available public report.
- `live-examples-20261001-{0,1}-diagnostics.json`: actual tasks, attempts, validation details, usage, and bounded validated excerpts.
- `live-examples-20261001-{api,worker}-bindings.json`: active contracts and deployed code hashes.
- `live-examples-20261001-evidence.json`: quotation/ownership checks and the two omission replays.
- `live-examples-20261001-integrity.json`: fresh/historical storage checks.
- `live-examples-20261001-runtime-final.log`: API, worker, and scheduler log capture.

No complete transcript, individual comment, secret, raw provider response, or hidden reasoning was exported into this report. No mock provider output supplied these two live results. The quote replay uses the application's current matcher and proves stored quotation linkage, not independent semantic correctness. Two different product examples cannot establish production token or latency parity.

[Open the published iPhone example](http://localhost:3000/r/GJCTgVIRjfS71QYZpCPnw9o6FvLNoyz8UqE2DEBoFQE).

**Conclusion:** launch and source/comment ingestion work in these examples. The comments synthesis blocker and aperture false rejection require correction; audit accuracy, material drawback retention, and product-field consistency also need stronger acceptance checks. The app cannot be described as fully validated on this evidence.
