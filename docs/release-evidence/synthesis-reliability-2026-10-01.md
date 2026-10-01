# Synthesis and evidence reliability — 2026-10-01

## Outcome

The requested corrections are implemented and deployed. Exactly two fresh DeepSeek V4 Flash analyses were admitted, with five videos and the saved English locale/options. Both published with full source coverage and evidence warnings; neither ended with a red error.

**Full live quality acceptance is not established.** Inspecting actual model output found negative battery observations under strengths in the first report, and reference-only disagreement text in the second. Follow-up corrections are deployed and verified offline. Completed reports remain unchanged, and no third paid analysis was launched. The final deployment was not retested with another paid analysis.

## Original failures

| Saved run | Evidence from logs | Correction |
|---|---|---|
| iPhone `cc4c09a4-7c99-436e-bad3-45619e78344b` | 5/5 reviews, 9 calls, 70,124 tokens. A supported `f/1.8` finding was pruned against `f1.8`. A battery comparison omitted the citation for the previous 18-hour result, although the model auditor accepted it. | Equivalent aperture parsing; dimension-specific quantities; per-source citation checks. The incompletely cited comparison remains excluded. |
| Samsung `5340afe7-5aa4-48f4-81c9-5e2363bb252a` | 5/5 reviews, 14 calls, 116,148 tokens. Synthesis failed twice at assertion 0 with `catalog_label_in_prose`; no audit or publication followed. | Validate citation ownership first, normalize owned prose aliases, isolate unresolved prose to one omitted assertion. Unknown/mixed citation ownership still fails. |

The original rejected Samsung response was not retained. Its exact wording cannot be recovered from the saved hash or validator code. Reconstructed formatting cases are labelled synthetic, not captured model responses.

## Implemented behavior

### Synthesis and repair

- `NormalizedBuyingSynthesis` derives ownership from known evidence references before prose rendering. It deduplicates references, renders owned source aliases as reviewer attribution, and removes owned citation-only annotations.
- Genuine product identifiers in the requested identity or owned quotations are protected, including E1/S1/S25 cases.
- Foreign/unresolved prose omits the affected assertion, with bounded indexed diagnostics. Invalid citation ownership fails validation with category, ownership and invalid-output hash.
- Reference-only observations or disagreement sides are omitted rather than published as “the cited excerpt.” These follow-up cases include synthetic reference inputs and the literal placeholder observed in the Samsung report.
- A zero-finding draft skips the paid first audit. It can enter the existing bounded correction/re-audit path only when validated central evidence remains.
- Failed final audits remain unpublished. Unchanged rejected findings prevent re-audit. No correction/re-audit attempt allowances increased.
- Audit validation retries now distinguish duplicate narrative rejection paths from invalid rejection spans, identifying the exact indexed field and output hash. Invalid raw responses are not retained.

### Evidence and report consistency

- Exact rational quantity checks distinguish aperture, duration, percentage, mass, capacity, energy, length, frequency, power, storage, resolution and frame rate. Explicit millisecond/microsecond latency conversions are supported.
- `f1.8` and `f/1.8` match; wrong apertures and units do not. `500 g` cannot support `500 mAh`, and `100 ms` cannot borrow support from `100 mAh`.
- Product identity exemptions cover identity spans, rather than exempting unrelated measurements that happen to share a product-name number.
- Each finding's quantities must be supported by citations belonging to every claimed source. Comparisons cannot borrow an uncited prior result. Semantic meaning, attribution, conditions and polarity still require model auditing.
- Deterministic rejection codes are recorded alongside actual model decisions.
- `ClassifiedVideoExtraction` adds internal kind/topic metadata within the existing per-video call. Public source-analysis fields remain unchanged. Strength means benefit, caveat means drawback, and context is neutral; a supported negative remains a caveat.
- At most four compact drawback priorities enter synthesis. Code reserves available distinct drawback topics within twelve total findings using owned quotations. When the same sole quotation was placed under strengths, reservation replaces that strength before displacing another finding. The auditor judges the resulting quotation and placement.
- Conservative processor aliases share conflict keys while component types and explicit variant scopes remain separate. Different cited values remain visible as conflicts; identical citations are deduplicated.
- Long-term/retrospective classification requires at least 30 explicitly supported ownership/use days. Battery runtime does not establish ownership duration; unknown duration receives no duration bonus.
- Existing full-coverage warning wording remains. Optional product-detail rejection stays independent of valid review retention.

### Compatibility and evaluations

Compiled adapters retain legacy extraction, synthesis, curator and audit snapshot contracts. Public APIs, report shapes, scoring rules, caption caching, model budgets and attempt caps are preserved.

Checked-in configuration evaluation and model-assignment evaluation now identify their results as **static contract checks**, with `live_accuracy_verified=false`. They do not invent perfect evidence metrics. Response evaluations retain independently prescribed positive/negative cases; fixture-supplied auditor decisions are explicitly separate from live accuracy.

## Verification evidence

| Verification | Result | What it establishes |
|---|---|---|
| Full isolated backend suite | 348 passed, 5 skipped; 73.72 seconds | Backend and integration regressions before the final follow-up guards. Two dependency deprecation warnings. |
| Final targeted evidence/synthesis/audit/correction/comments tests | 111 passed | Final reference-only guard, precise retry diagnostics and latency units, plus affected regressions. |
| Phase 6/hash contract tests | Passed after successor prompt hash updates | Compiled contract and checked-in configuration consistency. |
| Host budget gate tests | 5 passed | Normal-call bounds include audience calls and existing repair allowances. |
| Ruff / targeted mypy | Passed; 8 source modules checked by mypy | Static checks on affected code. |
| Seven worker scenarios | All passed, including a final fixture-only replay after the main follow-up guards | Actual request counting, terminal gates, fresh lineage, review context, graph projection idempotency and transaction behavior. |
| 24 distinct matched offline cases | Passed against commit `7a3aa9c`; zero paid calls | Presentation/quantity regressions, valid finding retention, no increase in unsupported retention, and estimated total-token accounting. |
| Context atlas / secret scan / diff checks | Passed | Updated contracts/maps and sanitized changes. |

### Worker request accounting

| Fixture scenario | Actual model requests | Correction calls | Re-audit calls | Published |
|---|---:|---:|---:|---|
| complete | 9 | 0 | 0 | Yes |
| comments | 14 | 0 | 0 | Yes |
| audit_uppercase_correction | 11 | 1 | 1 | Yes |
| audit_correction | 11 | 1 | 1 | Yes |
| audit_fail | 11 | 1 | 1 | No |
| audit_empty_correction | 10 | 1 | 0 | No |
| audit_unchanged_correction | 10 | 1 | 0 | No |

Every scenario retained five source analyses and used zero product-information/knowledge-curator model calls. These are local provider fixtures with expected audit decisions, not live model accuracy tests.

### Offline budget comparison

The 24 cases pair two captured product inputs with twelve distinct formatting, citation and quantity scenarios. Altered quotation/formatting inputs are synthetic. Direct positive excerpts include Siri usefulness and a positive camera observation; negative cases include wrong units and failed final audits.

- Baseline estimated mean total tokens: **92,494.67**.
- Candidate estimated mean total tokens: **92,264.08** (approximately 0.25% lower).
- Every validation retry and bounded correction/re-audit used by the replay is counted. Changed review prompt/schema costs are counted, including maximum-length kind/topic metadata for every claim.
- Supplied source coverage remains five in both sides; valid retained finding count never decreases and unsupported retained count never increases in these cases.
- Local p95 processing: approximately **9.82 ms baseline / 49.17 ms candidate** on the recorded final replay. This measures local validation/envelope work, not provider or end-to-end production latency. Local processing is slower; production token/latency parity is unverified.

The replay uses prescribed audit results and estimates outputs. It cannot establish that the live auditor will make the same semantic decisions or that all buying topics will be retained in future reports.

## Actual live tests

Elapsed time below is database creation-to-completion time, including admission/scheduling. Usage values come from actual chat ledger entries, not estimates. All recorded actual models were `deepseek/deepseek-v4-flash`.

| Product/options | Run | Coverage | Actual calls | Tokens | Elapsed | Publication |
|---|---|---:|---:|---:|---:|---|
| iPhone 18 Pro Max, comments off | `f793c4b7-f0ad-407c-bce0-c73315f3819b` | 5/5 | 9 | 66,905 | 77.47 s | Published, partial / audit warnings |
| Samsung S25 Ultra, comments on | `99cb1fb7-9f30-46ec-b89a-c8d9393b4c49` | 5/5 plus 5 audience analyses | 15 | 118,655 | 86.09 s | Published, partial / audit warnings |

Both report pages, public JSON, PDFs and graph endpoints returned HTTP 200; PDF headers were valid. These transport checks do not approve finding meaning.

### iPhone findings and follow-up

[Published iPhone report](http://localhost:3000/r/IsyLJk-sE0lSVFtLbi59EbBHJsy2gj2te0BfH08s7Ig)

- No model validation retry, correction call or re-audit call.
- The auditor marked all twelve findings supported. Deterministic checks removed a disagreement with overlapping source ownership. The model flagged the summary's uncited “heavy use” qualification, and a safe summary replaced it.
- Four material drawback quotations survived: build material, camera complexity, weight qualification, and ergonomic compromise.
- **Human inspection failed section-polarity acceptance:** pros at original indices 2 and 3 described battery dropping substantially/dying quickly. Their matching citations establish negative observations, not benefits. Review extraction also labelled the drain claim as a strength.
- A drawback quotation was duplicated between a strength and an injected caveat. Reservation now replaces the misplaced same-citation strength.
- Follow-up prompts define kind explicitly and tell the auditor that section placement is part of polarity. Captured validated outputs and independent human labels are in `backend/tests/fixtures/live_finding_kind.json`. The human-supplied regression decisions are not presented as live model decisions.

This completed report remains unchanged. Its fixes were not retested by another paid iPhone analysis.

### Samsung findings and follow-up

[Published Samsung report](http://localhost:3000/r/ZgXYUcP3vnduylS8HTjIuLxRKYy_ZpqqToHLnmEuyug)

- Source acquisition and all five audience analyses succeeded. Synthesis completed on its first call; the original report-wide prose-guard failure did not recur.
- Audit attempt 1 failed validation with the combined historical code text “duplicate narrative rejection or invalid rejection span.” Its invalid response was not retained, so the exact rejected clause and which branch triggered are unknown. The existing second attempt succeeded. This is the fifteenth call; no correction or re-audit model calls followed.
- The final auditor marked twelve findings supported. A summary camera claim was omitted because it was not represented in retained findings, and unresolved buyer-guidance prose was omitted. The safe summary preserved cited strengths and caveats.
- Material drawbacks include S Pen Bluetooth removal, screen-protector conditions, heavy-gaming battery duration, low-light noise, incremental upgrade concerns and attributed AI-pricing uncertainty. Final balance was two strengths and ten caveats; some topics repeat across sources. This does not prove balanced buying-topic coverage.
- **Human inspection found a presentation defect:** both disagreement sides displayed only “the cited excerpt.” The final guard excludes such placeholder observations/sides before auditing. Synthetic reference-only cases and the observed literal placeholder are tested.
- Semantic qualification remains a live accuracy concern: the first battery strength includes “for typical mixed use,” while its short cited excerpt states the reviewer's all-day result without explicitly describing that workload. No deterministic lexical rule is claimed to prove such conditions.

This completed report remains unchanged. The final placeholder/diagnostic guards were not tested with another paid Samsung analysis. The live reports therefore demonstrate recovered publication, not complete semantic acceptance.

## Final rollout

API, worker, scheduler and migration images were rebuilt. The runtime was restarted and became healthy; API live/readiness and frontend checks returned 200.

Active workflow **29**, ID `83b41340-eb6f-4620-9d23-feab8f56be7f`, uses the successor adapters and DeepSeek V4 Flash. The verified final binding versions are:

| Task role | Version | Output contract |
|---|---:|---|
| Research coordinator | 8 | QueryPlan |
| Source curator | 9 | SourceCuration |
| Review analyst | 16 | ClassifiedVideoExtraction |
| Audience analyst | 8 | AudienceAnalysisDraft |
| Synthesis / correction | 19 | NormalizedBuyingSynthesis |
| Audit / re-audit | 18 | FindingAuditResult |

Actual active binding hashes and host/deployed hashes were compared for API, worker and scheduler, including analysis and affected admin files. The budget hash remains `44683a65f1d317b6de624ea6dc662cdba84c407401e2a5176efc4821de17cdf2`; agent execution/generation limits and every workflow attempt allowance match the pre-change capture. Normal bounds remain nine without comments and fourteen with five audience analyses; validation retries and at most one correction plus one re-audit remain separately bounded.

The first live sample used workflow 27; the second used workflow 28. Workflow 29 includes follow-up fixes and conservative prompt compaction after those samples. Do not attribute the two live results to paid verification of workflow 29.

Verification manifests and full diagnostics are local ignored artifacts under `.audit-cache/`, including `reliability-deployed-final.json`, `reliability-active-bindings-final.json`, `reliability-budget-final.json`, `synthesis-reliability-replays.json`, both `reliability-live-*-diagnostics.json` files and `reliability-integrity-final.json`.

## Storage and Docker limitations

Read-only comparison confirmed that status, timestamps, options, usage, publication and stored audit results of all four original/new completed records were unchanged. Their Markdown bodies passed hash reads; fresh iPhone/Samsung workspaces had 119/139 valid node versions and no missing bodies.

The separate six historical missing bodies remain quarantined: five versions in `21927512-b747-4c56-ac0e-6be83a4b9b54` and one in `66677476-8812-465c-bf4d-9ace19f86345`. No verified matching backups were available; no bodies were invented or restored.

Docker's VM is limited to 4 GB. Running main and isolated stacks concurrently caused confirmed kernel OOM kills of Java/Neo4j and engine failures. Verification subsequently ran with the idle main stack stopped. Stale Windows Docker runtime socket directories were moved aside with their originals preserved; data volumes were not reset. Only disposable `reviewlens-reliability-20261001` test volumes were removed. The main application and completed records were preserved.

## Acceptance boundary

Implementation, offline regressions, publication gates, deployment and readiness checks pass. Both authorized live reports published, but complete live semantic acceptance remains unresolved for the reasons above. Genuine evidence warnings remain visible. Two samples do not guarantee future availability, correct model judgments, balanced topic coverage, or production token/latency parity.
