# ReviewLens Repository Agent Guide

## Purpose

Use this repository as an Obsidian-style code and context vault. The goal is to reach the smallest sufficient set of specifications and source files for a task, not to preload the repository.

The code currently present is the legacy V1 proof of concept. The documents in `context/00` through `context/27` define Target V2 and do not claim that V2 exists in code.

For phased rebuild work, use [the single execution controller](REBUILD_PHASES.md). An assigned phase is an implementation goal: reason from its linked contracts, plan against the current repository, execute and verify the work, then stop at that phase boundary. Do not stop after planning or mark a phase complete without recorded evidence.

## Required navigation protocol

1. Read [the V2 context home](context/00_INDEX_AND_PROJECT_OVERVIEW.md).
2. Classify the task using its **Task reading bundles** table.
3. Read [the codebase map](context/codebase/00_CODEBASE_MAP.md).
4. Open exactly one area map: frontend, backend, or infrastructure.
5. Read the listed source files that directly participate in the requested behavior.
6. Expand to adjacent code or context only when an import, data contract, or failing test crosses that boundary.
7. Before editing, read the closest nested `AGENTS.md`; nested instructions override this guide for their directory.

For a narrow fix, do not read all V2 notes or all source files. A full-vault read is reserved for architecture audits, broad migrations, and release-readiness reviews.

## Area router

| Task area | Start with code map | Minimum V2 context |
|---|---|---|
| Public page, components, styling | [Frontend code map](context/codebase/01_FRONTEND_CODE_MAP.md) | `19`, `21`, then `16` for data |
| FastAPI route, schemas, SSE | [Backend code map](context/codebase/02_BACKEND_CODE_MAP.md) | `16`, plus `06` and `17` |
| YouTube/transcript/comments | [Backend code map](context/codebase/02_BACKEND_CODE_MAP.md) | `04`, `07`, `18` |
| Agents, prompts, scoring | [Backend code map](context/codebase/02_BACKEND_CODE_MAP.md) | `05`, `06`, `10`, `11` |
| OpenRouter/model/provider work | [Backend code map](context/codebase/02_BACKEND_CODE_MAP.md) | `12`, `13`, `14` |
| PostgreSQL/Redis/Neo4j/Qdrant/workers | [Infrastructure code map](context/codebase/03_INFRASTRUCTURE_CODE_MAP.md) | `03`, `15`, `23` |
| Docker, env, scripts, CI | [Infrastructure code map](context/codebase/03_INFRASTRUCTURE_CODE_MAP.md) | `17`, `23`, `24` |
| Cross-layer V2 feature | [Contract traceability](context/codebase/04_CONTRACT_TRACEABILITY.md) | Only the feature row's linked notes |

## Search before expansion

Use `rg` before opening additional files:

```powershell
rg -n "ExactSymbol|endpoint|event_name" backend frontend context
rg --files backend frontend context
```

Prefer symbol/import evidence over filename guesses. The code maps are routing indexes, not substitutes for reading the exact implementation being changed.

## Repository safety

- Preserve user-owned uncommitted changes and inspect `git status --short` before editing.
- Keep V1 behavior intact until the migration plan explicitly replaces it.
- Do not copy legacy direct-provider, stateless-run, fixed-three-video, or client-side provider-selection behavior into V2.
- Route all new V2 HTTP contracts through `/api/v2`.
- Never expose secrets or include their values in notes, output, fixtures, or commits.
- Use the verification level defined in `context/25_AGENT_BUILD_INSTRUCTIONS.md`.

## Keep the atlas current

When a change adds, removes, renames, or materially repurposes a source file, update the relevant `context/codebase` map in the same change. When a behavior crosses layers, update `04_CONTRACT_TRACEABILITY.md`. Do not add one Markdown note per function; maps should point to real source files and stable contracts.
