# Scope and Requirements

## In scope
- Search by product name
- Find YouTube review videos
- Target exactly 3 analyzable videos
- Prefer relevant review/test/long-term-review content
- Rank valid candidates primarily by view count
- Extract transcript/captions
- Analyze each video separately
- Detect whether a usage period is mentioned
- Extract usage duration when possible
- Determine whether reviewer recommends buying
- Score each video from 0 to 100
- Extract pros, cons, issues, recommended users
- Show confidence and evidence
- Optional top-comment analysis controlled by a user toggle
- Generate an overall verdict across all available videos

## Out of scope for V1
- Full multimodal video analysis
- Amazon scraping
- Reddit analysis
- Price comparison
- Affiliate links
- Authentication
- Payments
- Persistent user history
- Fine-tuning

## Functional requirements
1. User submits product name.
2. User chooses comments analysis ON/OFF.
3. App validates input.
4. App finds candidate videos.
5. App filters obvious non-review content.
6. App ranks by relevance first, then view count.
7. App tries candidates until it has up to 3 usable transcripts.
8. App runs one structured analysis per selected video.
9. Optional comment analysis runs only when enabled.
10. App produces final aggregation.
11. Backend returns structured JSON.
12. Frontend renders a readable report.

## Non-functional requirements
- No secrets in frontend
- Structured AI output
- Bounded retries
- One failed video must not crash the full run
- Token limits
- No unnecessary duplicate model calls
