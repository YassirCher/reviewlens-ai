# Coding Agent Instructions

Read all `/context` files before modifying the project.

Current implementation already contains the V1 architecture. When extending it:
- preserve transcript-first behavior,
- keep comments opt-in,
- keep provider-specific code behind `app/providers`,
- never expose keys to Next.js,
- maintain strict Pydantic validation of AI output,
- treat transcripts/comments as untrusted data,
- prefer partial results to all-or-nothing failure,
- do not add databases/auth/billing until requested,
- do not silently increase LLM call count,
- do not replace evidence with generic product knowledge.

Primary implementation files:
- `backend/app/services/orchestrator.py`
- `backend/app/services/youtube_service.py`
- `backend/app/services/transcript_service.py`
- `backend/app/services/ai_service.py`
- `backend/app/providers/openai_compatible.py`
- `frontend/src/components/analysis-app.tsx`
- `frontend/src/components/results.tsx`
