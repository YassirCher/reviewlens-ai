# Architecture Decisions

## ADR-001 — Transcript-first
Status: Accepted

Reason:
Cheaper, faster, easier to ground, and appropriate for a free-tier POC.

## ADR-002 — Three videos
Status: Accepted

Reason:
Predictable cost and simple comparison.

## ADR-003 — Comments opt-in
Status: Accepted

Reason:
Protect quota and reduce noise.

## ADR-004 — Deterministic pipeline
Status: Accepted

Reason:
More reliable and testable than an unrestricted agent.

## ADR-005 — Provider abstraction
Status: Accepted

Reason:
Free quotas may change. Grok/xAI, OpenRouter, OpenAI, Groq, or another provider should be swappable.

## ADR-006 — Evidence-first output
Status: Accepted

Reason:
Purchase recommendations should show supporting evidence.
