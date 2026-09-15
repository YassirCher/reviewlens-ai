---
id: RL-V2-REBUILD-PHASES
title: ReviewLens V2 Rebuild Phases
type: execution-map
status: target-v2
domain: delivery
tags:
  - reviewlens-v2
  - execution/phases
  - delivery/rebuild
depends_on:
  - "[[context/00_INDEX_AND_PROJECT_OVERVIEW]]"
  - "[[context/24_MIGRATION_AND_ROADMAP]]"
  - "[[context/25_AGENT_BUILD_INSTRUCTIONS]]"
related:
  - "[[context/22_TESTS_EVALUATIONS_ACCEPTANCE]]"
  - "[[context/26_ARCHITECTURE_DECISIONS]]"
  - "[[context/codebase/00_CODEBASE_MAP]]"
read_when:
  - Starting, continuing, reviewing, or handing off the ReviewLens V2 rebuild.
---

# ReviewLens V2 Rebuild Phases

**Status: Target V2 execution map**

## Purpose

This is the single phase controller for rebuilding ReviewLens V2. It intentionally defines outcomes rather than code-level task lists. When assigned a phase, the coding agent must use the phase goal and linked contracts to reason about the current repository, create its own implementation plan, execute that plan, verify the result, and continue until the phase is achieved.

The numbered specifications in `context/00` through `context/27` remain authoritative. This file sequences their implementation without duplicating or weakening their contracts.

## Command to give an agent

> Execute Phase N from [[REBUILD_PHASES]]. Read its linked context, inspect the current repository, formulate a concrete implementation and verification plan, then carry it through until the phase achievement condition is satisfied. Do not stop after presenting the plan, and do not begin a later phase.

Replace `N` with the phase number. Extra instructions should describe a real constraint only; the agent is responsible for deriving the technical plan.

## Phase execution contract

For every assigned phase, the agent must:

1. Read [[context/00_INDEX_AND_PROJECT_OVERVIEW]], this file, the phase's source-of-truth notes, and the smallest relevant code maps under `context/codebase`.
2. Inspect the current code, tests, configuration, dirty worktree, and the closest `AGENTS.md` before planning changes.
3. Produce a concise phase plan derived from evidence in the repository. The plan must cover implementation, migration safety, tests, and documentation/map maintenance.
4. Execute the plan instead of treating the request as planning-only. It may refine the plan as tests or repository evidence reveal new work.
5. Preserve working V1 behavior until a phase explicitly changes the compatibility boundary. Never overwrite user-owned changes or expose secret values.
6. Verify the complete phase achievement condition using the standards in [[context/22_TESTS_EVALUATIONS_ACCEPTANCE]]. A file existing or a happy-path demo is not sufficient evidence.
7. Update affected code maps, traceability, durable decisions, and the tracker in this file. Mark a phase complete only after recording its verification evidence.
8. Stop at the phase boundary and hand off the outcome, changed files, migrations, tests, configuration names, degraded behavior, and remaining risks as required by [[context/25_AGENT_BUILD_INSTRUCTIONS#Handoff]].

If a phase is too large for one working session, the agent may create milestones in its working plan, but it must not invent a competing roadmap or declare the phase complete early. If a contract conflict appears, resolution order is: accepted ADR, canonical domain contract, integration/UI contract, then roadmap prose.

## Status rules

- `complete`: the achievement condition is satisfied and verification evidence is recorded.
- `ready`: every dependency is complete and implementation may start.
- `queued`: a dependency is not complete yet.
- `in progress`: work has begun but the achievement condition has not passed.
- `blocked`: completion requires missing authority, external coordination, or an unavailable dependency; record the exact blocker without weakening the goal.

Only one phase may be `in progress`. A later phase may receive a minimal enabling change when technically unavoidable, but it cannot be marked complete before its declared dependencies.

## Phase tracker

| Phase | Outcome | Status | Completion evidence |
|---|---|---|---|
| 0 | V2 contract and repository navigation baseline | complete | Completed 2026-09-15: `python scripts/check_context.py` passed; `git diff --check` passed; `cd backend && python -m pytest tests -q` passed (3 tests); runtime source remained unchanged |
| 1 | Platform and persistence foundation | complete | Completed 2026-09-15: `python scripts/check_context.py` passed; `git diff --check` passed; `cd backend && .venv/Scripts/python.exe -m pytest tests -q` passed (11 tests, 8 integration tests skipped); `.\backend\.venv\Scripts\python.exe scripts\check_phase1.py` passed (19 isolated PostgreSQL/Redis tests, clean full-stack health, V1 smoke, projection degradation, and Redis/PostgreSQL loss); pre-existing frontend changes remained untouched and `context/` remained untracked |
| 2 | Durable run and task execution backbone | complete | Completed 2026-09-15: `python scripts/check_context.py`; `git diff --check`; `cd backend && .venv\Scripts\python.exe -m pytest tests -q`; `python scripts/check_phase2.py` (38 isolated PostgreSQL/Redis/Celery tests, migration downgrade/re-upgrade, full-stack V1 smoke, and deterministic success/retry/cancel fixtures). |
| 3 | OpenRouter gateway and LLMOps accounting core | ready | Phase 2 completed and verified on 2026-09-15. |
| 4 | Reconstructable context graph and retrieval | queued | Pending |
| 5 | Typed YouTube research toolchain | queued | Pending |
| 6 | Bounded multi-agent analysis workflow | queued | Pending |
| 7 | Public V2 API and report lifecycle | queued | Pending |
| 8 | Public research experience | queued | Pending |
| 9 | Admin LLMOps control plane | queued | Pending |
| 10 | Whole-system hardening and acceptance | queued | Pending |
| 11 | Compatibility, V2 cutover, and operational proof | queued | Pending |
| 12 | Legacy retirement and final V2 conformance | queued | Pending |

## Phase 0 — V2 contract and navigation baseline

**Depends on:** None.

**Goal:** Establish one coherent Target V2 specification and an Obsidian-style repository atlas that lets an agent reach the necessary contracts and source files without reading the entire vault.

**Source of truth:** [[context/00_INDEX_AND_PROJECT_OVERVIEW]], [[context/01_PRODUCT_SCOPE]], [[context/02_ROLES_AND_USER_FLOWS]], [[context/24_MIGRATION_AND_ROADMAP]], [[context/25_AGENT_BUILD_INSTRUCTIONS]], [[context/26_ARCHITECTURE_DECISIONS]], [[context/27_REFERENCE_SOURCES]], and [[context/codebase/00_CODEBASE_MAP]].

**Achieved when:** The 28 Target V2 notes are coherent and navigable, legacy and target behavior are unmistakably separated, source maps resolve to the current repository, and automated documentation/link checks pass without changing runtime behavior.

## Phase 1 — Platform and persistence foundation

**Depends on:** Phase 0.

**Goal:** Give V2 a secure, locally reproducible platform in which transactional state, immutable configuration versions, audit history, secrets, service health, and infrastructure dependencies have durable ownership while V1 remains usable as a migration reference.

**Source of truth:** [[context/03_SYSTEM_ARCHITECTURE]], [[context/15_DATA_MODEL_AND_STORAGE]], [[context/17_SECURITY_AUTH_AND_RATE_LIMITS]], [[context/23_DEVOPS_AND_CONFIGURATION]], and [[context/26_ARCHITECTURE_DECISIONS]].

**Achieved when:** The complete Docker Compose profile can start from documented environment configuration; migrations build the required PostgreSQL foundation from empty state; Redis, worker, scheduler, and projection services have truthful health/degraded-state behavior; seeded admin authentication and audit foundations are secure; and automated foundation tests pass without regressing V1.

## Phase 2 — Durable run and task execution backbone

**Depends on:** Phase 1.

**Goal:** Establish the orchestrator-owned execution substrate that persists analyses, deterministic DAG tasks, attempts, cancellation, retries, configuration snapshots, outbox events, budgets, and resumable progress independently of API or worker restarts.

**Source of truth:** [[context/06_WORKFLOW_TASK_RUNTIME]], [[context/15_DATA_MODEL_AND_STORAGE]], [[context/16_API_AND_EVENT_CONTRACTS]], [[context/18_FAILURE_HANDLING]], and [[context/23_DEVOPS_AND_CONFIGURATION]].

**Achieved when:** A background fixture workflow can be created, executed, observed, resumed by event ID, retried safely after duplicate delivery or interruption, cancelled without downstream dispatch, and reconstructed from PostgreSQL with state committed before progress is emitted.

## Phase 3 — OpenRouter gateway and LLMOps accounting core

**Depends on:** Phase 2.

**Goal:** Make paid OpenRouter the only V2 gateway for chat and embeddings, with current searchable model metadata, endpoint-aware provider controls, strict capability validation, versioned routing policies, enforceable budgets, and exact per-attempt usage attribution.

**Source of truth:** [[context/11_PROMPT_AND_OUTPUT_CONTRACTS]], [[context/12_OPENROUTER_INTEGRATION]], [[context/13_MODEL_AND_PROVIDER_ROUTING]], [[context/14_LLMOPS_ANALYTICS_AND_BUDGETS]], [[context/17_SECURITY_AUTH_AND_RATE_LIMITS]], and [[context/18_FAILURE_HANDLING]].

**Achieved when:** Mocked contract tests prove current catalog refresh, endpoint discovery, all-provider and restricted routing, structured-output rejection, embedding calls, retries and documented OpenRouter failures, budget reservation/reconciliation, and complete token/cost/model/actual-provider attribution; optional live smoke tests remain explicitly capped and secret-safe.

## Phase 4 — Reconstructable context graph and retrieval

**Depends on:** Phase 3.

**Goal:** Create the Obsidian-like runtime knowledge environment in which validated Markdown node bodies and PostgreSQL relations are authoritative, Neo4j and Qdrant are rebuildable projections, and agents receive bounded provenance-labelled context packets instead of unstructured repository-scale text.

**Source of truth:** [[context/08_CONTEXT_GRAPH]], [[context/09_CONTEXT_RETRIEVAL]], [[context/12_OPENROUTER_INTEGRATION]], [[context/15_DATA_MODEL_AND_STORAGE]], and [[context/18_FAILURE_HANDLING]].

**Achieved when:** Typed nodes and relations survive atomic write/version/export/reconciliation flows; path and frontmatter validation prevent unsafe state; projection rebuilds reproduce graph/vector indexes from authoritative data; retrieval produces deterministic manifests within token budgets; and the system degrades truthfully when either projection is unavailable.

## Phase 5 — Typed YouTube research toolchain

**Depends on:** Phase 4.

**Goal:** Convert YouTube discovery, source selection, details, transcripts, optional comments, graph operations, evidence checks, and deterministic scoring into schema-validated, allowlist-ready, fully audited tools that treat all retrieved content as untrusted evidence.

**Source of truth:** [[context/04_YOUTUBE_ANALYSIS_PIPELINE]], [[context/07_TOOL_REGISTRY]], [[context/08_CONTEXT_GRAPH]], [[context/10_ANALYSIS_SCHEMAS_AND_SCORING]], and [[context/18_FAILURE_HANDLING]].

**Achieved when:** Deterministic fixtures prove source relevance/diversity, default-five and three-to-eight bounds, transcript fallback and language metadata, comments opt-in behavior, evidence provenance, scoring behavior, invocation audit records, injection resistance, and useful partial-source outcomes without arbitrary code or network tools.

## Phase 6 — Bounded multi-agent analysis workflow

**Depends on:** Phase 5.

**Goal:** Deliver the seven versioned agent roles as a deterministic, orchestrator-owned analysis DAG whose prompts, schemas, model policies, provider policies, budgets, retries, and curated tool permissions are immutable for each run and whose published report claims are evidence-valid.

**Source of truth:** [[context/05_AGENT_CATALOG]], [[context/06_WORKFLOW_TASK_RUNTIME]], [[context/07_TOOL_REGISTRY]], [[context/10_ANALYSIS_SCHEMAS_AND_SCORING]], [[context/11_PROMPT_AND_OUTPUT_CONTRACTS]], and [[context/18_FAILURE_HANDLING]].

**Achieved when:** Representative fixture runs exercise per-video fan-out, optional audience analysis, context curation, consensus, correction, quality audit, deterministic scoring/publication gates, cancellation, retries, and partial results; every task and attempt is inspectable; agents cannot self-delegate or exceed their immutable tool/configuration boundaries; and critical evidence/injection evaluations pass.

## Phase 7 — Public V2 API and report lifecycle

**Depends on:** Phase 6.

**Goal:** Expose a stable `/api/v2` public contract for creating and cancelling analyses, reading persistent run/task/report/usage/evidence state, and resuming sanitized progress streams while enforcing unguessable access, quotas, budgets, idempotency, and safe failure semantics.

**Source of truth:** [[context/02_ROLES_AND_USER_FLOWS]], [[context/10_ANALYSIS_SCHEMAS_AND_SCORING]], [[context/16_API_AND_EVENT_CONTRACTS]], [[context/17_SECURITY_AUTH_AND_RATE_LIMITS]], and [[context/18_FAILURE_HANDLING]].

**Achieved when:** API and security tests prove the documented lifecycle, serializers, report-token isolation, total-token visibility, private cost boundaries, resumable SSE, non-enumerating failures, rate and spending limits, concurrency control, kill switch, partial/degraded reports, and persistence across process restarts.

## Phase 8 — Public research experience

**Depends on:** Phase 7.

**Goal:** Replace the legacy public screen with a calm, evidence-led research experience that makes product submission, live task progress, persistent reports, source inspection, token totals, and a simplified accessible evidence map clear on every target viewport.

**Source of truth:** [[context/01_PRODUCT_SCOPE]], [[context/02_ROLES_AND_USER_FLOWS]], [[context/10_ANALYSIS_SCHEMAS_AND_SCORING]], [[context/16_API_AND_EVENT_CONTRACTS]], [[context/18_FAILURE_HANDLING]], [[context/19_PUBLIC_UI_SPEC]], and [[context/21_DESIGN_SYSTEM]].

**Achieved when:** The public journeys work end to end against real V2 contracts, including reconnection and all loading/empty/error/partial/degraded states; claims visibly lead to evidence without leaking admin-only data; keyboard and screen-reader alternatives are complete; and accessibility, reduced-motion, long-content, zoom, responsive, and visual checks pass at 375, 768, 1024, and 1440 pixels.

## Phase 9 — Admin LLMOps control plane

**Depends on:** Phases 7 and 8.

**Goal:** Deliver the protected research cockpit through which the single admin can understand and govern runs, agents, tasks, workflows, tools, OpenRouter models/providers, budgets, usage, context nodes, projections, settings, and audit history without mutating published run configuration.

**Source of truth:** [[context/05_AGENT_CATALOG]], [[context/08_CONTEXT_GRAPH]], [[context/13_MODEL_AND_PROVIDER_ROUTING]], [[context/14_LLMOPS_ANALYTICS_AND_BUDGETS]], [[context/16_API_AND_EVENT_CONTRACTS]], [[context/17_SECURITY_AUTH_AND_RATE_LIMITS]], [[context/20_ADMIN_UI_SPEC]], and [[context/21_DESIGN_SYSTEM]].

**Achieved when:** Secure admin flows work end to end for model search and endpoint/provider policy, draft/validate/publish/rollback configuration, agent/task traces, tool audits, graph inspection and accessible alternatives, projection health, analytics breakdowns, budgets, credit availability states, kill switch, sessions, settings, and audit history across all required states and target viewports.

## Phase 10 — Whole-system hardening and acceptance

**Depends on:** Phase 9.

**Goal:** Prove that the integrated V2 system is secure, observable, recoverable, accessible, budget-safe, evidence-grounded, and operationally reproducible under normal, partial, hostile, duplicate, interrupted, and unavailable-dependency conditions.

**Source of truth:** [[context/17_SECURITY_AUTH_AND_RATE_LIMITS]], [[context/18_FAILURE_HANDLING]], [[context/22_TESTS_EVALUATIONS_ACCEPTANCE]], [[context/23_DEVOPS_AND_CONFIGURATION]], [[context/25_AGENT_BUILD_INSTRUCTIONS]], and every canonical feature note implicated by a failing acceptance case.

**Achieved when:** The complete automated test and evaluation strategy passes from a clean environment; documented OpenRouter, YouTube, worker, stream, database, projection, authentication, rate-limit, injection, accessibility, and responsive failure cases behave as specified; usage totals reconcile; recovery and rebuild procedures are demonstrated; and no acceptance criterion is waived or represented by a placeholder.

## Phase 11 — Compatibility, V2 cutover, and operational proof

**Depends on:** Phase 10.

**Goal:** Move the default product path to V2 through an explicitly temporary compatibility boundary while preserving rollback, truthful telemetry, budget control, active-run safety, and evidence that the new system is stable enough to replace V1.

**Source of truth:** [[context/16_API_AND_EVENT_CONTRACTS]], [[context/18_FAILURE_HANDLING]], [[context/22_TESTS_EVALUATIONS_ACCEPTANCE]], [[context/23_DEVOPS_AND_CONFIGURATION]], [[context/24_MIGRATION_AND_ROADMAP]], and [[context/26_ARCHITECTURE_DECISIONS]].

**Achieved when:** The public root uses V2; the legacy analyze path delegates only through the documented adapter and exposes deprecation signals; rollback and kill-switch procedures are verified; configuration migration is documented; active and queued work remains safe; and observed error, latency, cost, token, evidence, and compatibility metrics satisfy the defined stable-window gate.

## Phase 12 — Legacy retirement and final V2 conformance

**Depends on:** Phase 11 and its real stable-window evidence.

**Goal:** Remove obsolete V1 runtime paths and deprecated direct-provider assumptions only after they have no active consumers, leaving a coherent V2 repository whose implementation, tests, configuration, documentation, and code atlas agree.

**Source of truth:** [[context/00_INDEX_AND_PROJECT_OVERVIEW]], [[context/22_TESTS_EVALUATIONS_ACCEPTANCE]], [[context/24_MIGRATION_AND_ROADMAP]], [[context/25_AGENT_BUILD_INSTRUCTIONS]], [[context/26_ARCHITECTURE_DECISIONS]], [[context/27_REFERENCE_SOURCES]], and [[context/codebase/04_CONTRACT_TRACEABILITY]].

**Achieved when:** Stable-window evidence authorizes removal; V1 routes, deprecated direct-provider configuration, dead migration adapters, and stale UI are gone; all 28 Target V2 specifications and acceptance criteria are implemented or explicitly superseded by an accepted ADR; the full clean-environment suite passes; code maps match the final repository; and README/architecture documentation truthfully describe the shipped system.

## Context coverage

Every numbered Target V2 note has an implementation home in at least one phase. Cross-cutting notes intentionally recur because their contracts must be enforced at each boundary rather than postponed to final testing.

| Context domain | Primary phases |
|---|---|
| Overview, scope, roles, decisions, references (`00`–`02`, `26`, `27`) | 0, 7, 8, 12 |
| Architecture and YouTube pipeline (`03`, `04`) | 1, 5 |
| Agents, runtime, and tools (`05`–`07`) | 2, 5, 6, 9 |
| Context graph and retrieval (`08`, `09`) | 4, 5, 9 |
| Schemas, scoring, prompts, OpenRouter, routing, analytics (`10`–`14`) | 3, 5, 6, 7, 8, 9 |
| Storage, API, security, failures (`15`–`18`) | 1, 2, 4, 7, 10 |
| Public UI, admin UI, and design system (`19`–`21`) | 8, 9 |
| Tests, operations, migration, and agent instructions (`22`–`25`) | 0, 1, 10, 11, 12 |

## Final rule

The rebuild is complete only when Phase 12 is `complete`. Passing an earlier demo, creating screens, or implementing the happy path does not satisfy the Target V2 context pack.
