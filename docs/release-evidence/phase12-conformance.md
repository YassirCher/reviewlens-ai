# Phase 12 final V2 conformance matrix

The matrix maps all 28 Target V2 specifications to implemented areas and automated evidence. `scripts/check_phase12.py` is the current acceptance entry point. Its stack mode runs the complete backend suite, including earlier contract tests retained under their original phase names.

| Spec | Contract | Implementation evidence | Passing test evidence |
|---|---|---|---|
| 00 | Index and overview | Repository maps and this matrix | `scripts/check_context.py` |
| 01 | Product scope | V2 only root, bounded research and reports | public API and browser suites |
| 02 | Roles and flows | anonymous owner, administrator, background processes | Phase 7/9/12 integration and browser tests |
| 03 | System architecture | PostgreSQL authority, Redis/Celery delivery, rebuildable projections | health, outage, backup, migration and recovery drills |
| 04 | YouTube pipeline | `app.tools.research`, typed YouTube client and evidence persistence | Phase 5 and failure matrix tests |
| 05 | Agent catalog | seven published role definitions and immutable versions | Phase 6/9 configuration tests |
| 06 | Workflow runtime | DAG snapshots, attempts, leases, outbox, cancellation and recovery | Phase 2/6/10 integration tests |
| 07 | Tool registry | fixed handlers, versioned allowlists and audited invocation | Phase 5/9 tests |
| 08 | Context graph | versioned nodes, edges, manifests and isolated workspaces | Phase 4/9 graph tests |
| 09 | Retrieval | bounded hybrid retrieval with PostgreSQL fallback | Phase 4 retrieval and projection degradation tests |
| 10 | Schemas and scoring | strict role outputs, deterministic aggregation and audit | Phase 6 workflow and Phase 10 golden suite |
| 11 | Prompt and output contracts | trusted task envelope, untrusted source delimiters and correction limit | gateway, injection and evaluation tests |
| 12 | OpenRouter integration | catalog client, policy gateway and native accounting | Phase 3/10/12 mocked provider tests |
| 13 | Model routing | published model policies and eligible endpoint validation | configuration, catalog and two Flash model stack tests |
| 14 | Analytics and budgets | reservations, usage ledger, reconciliation and aggregates | Phase 3/9/10 budget and analytics tests |
| 15 | Data model and storage | Alembic through `20260920_0009`; immutable version and history records | empty to head and downgrade/re-upgrade stack cycle |
| 16 | API and events | `/api/v2`, filter bound cursors, SSE replay and safe envelopes | Phase 7/9/10 API tests |
| 17 | Security | auth, CSRF, CORS, request bounds, redaction and safe paths | Phase 1/9/10 security tests and secret scan |
| 18 | Failures | bounded retry, safe partials, interruption and dependency recovery | Phase 2/5/6/10 recovery tests and outage drills |
| 19 | Public UI | root intake, progress, reports and evidence alternatives | public Playwright, Axe, zoom and responsive journeys |
| 20 | Admin UI | runs, routing, versions, budgets, knowledge, audits and retirement history | admin Playwright and Phase 9 API tests |
| 21 | Design system | graphite tokens, accessible controls, tables, charts and graph alternatives | frontend lint/build and browser accessibility suite |
| 22 | Tests and evaluation | versioned golden suite and clean environment gate | local tests plus `check_phase12.py` modes |
| 23 | DevOps and configuration | generated secrets, isolated Compose, health and cleanup | stack gate, dependency audits and log scan |
| 24 | Migration and roadmap | V2 cutover, compatibility history and evidence gated retirement | Phase 11 records, Phase 12 exporter and runbook |
| 25 | Agent build instructions | scoped navigation and protected user files | context audit and protected byte inventory |
| 26 | Architecture decisions | durable snapshots, OpenRouter only routing and application rollback | ADR vault plus migration and rollback drills |
| 27 | Reference sources | source inventory retained in the context vault | context link and Canvas audit |

Local acceptance passed on 2026-09-20: static mode passed 93 local backend tests, five frontend unit tests, lint, type, build, dependency, context, secret, and diff gates; stack mode passed 150 containerized backend tests, three real-stack browser journeys, migration and recovery drills, and exact mocked dispatch through both approved DeepSeek Flash slugs; browser-only mode passed 21 accessibility and responsive journeys. No live or paid provider call was made.

The working repository is complete without deployment state. Before deploying Phase 12 over a real Phase 11 environment, an operator must export `docs/release-evidence/phase12-cutover.json` from the authoritative production database and run `scripts/check_phase12.py --evidence-only`. That deployment gate rejects a missing, changed, test-only, weakened, incomplete, or stale artifact.
