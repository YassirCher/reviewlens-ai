# Acceptance Criteria

V1 is accepted when:
- [x] User enters a product name.
- [x] User can enable/disable top-comment analysis.
- [x] Comment analysis is OFF by default.
- [x] User can choose Auto/OpenRouter/xAI when configured.
- [x] API keys remain server-side.
- [x] App searches YouTube and filters obvious non-review candidates.
- [x] App attempts to obtain 3 usable transcripts and falls back to lower-ranked candidates if needed.
- [x] Each video returns score, verdict, usage period if stated, pros/cons, confidence and evidence.
- [x] Optional comments return audience sentiment and recurring feedback.
- [x] Final report returns score, verdict, confidence, consensus, disagreements and fit/avoid audiences.
- [x] UI receives live progress through SSE.
- [x] Failure of final aggregation does not destroy per-video work.
- [x] Project includes full context documentation and Docker/local run instructions.
