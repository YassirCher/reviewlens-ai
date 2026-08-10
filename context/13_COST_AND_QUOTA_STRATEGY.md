# Cost and Quota Strategy

V1 is optimized for free/limited API quotas.

Rules:
- Only three videos are analyzed.
- Comments are OFF by default.
- Optional comments are folded into each existing per-video LLM call.
- Default OpenRouter model is `openrouter/free`.
- Transcript and comment character limits are enforced.
- No extra LLM transcript-summarization calls are made; long transcripts are compacted heuristically while preserving intro, conclusion and high-signal usage/recommendation passages.
- Structured outputs reduce parse/retry waste.
- Final consensus has a deterministic fallback.

Typical model call budget:
- 3 per-video analysis calls
- 1 final consensus call
- ~4 total

If the fourth call fails due to free-tier quota/availability, the user still receives a deterministic consensus from the three successful analyses.
