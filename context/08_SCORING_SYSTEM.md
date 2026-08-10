# Scoring System

## Principle
Scores should help comparison without pretending to be perfectly objective.

## Per-video score
The AI returns:
- Reviewer sentiment: 0-100
- Purchase recommendation: 0-100
- Evidence quality/confidence: 0-100

Suggested V1 score:
`product_score = 0.45 * purchase_recommendation + 0.35 * reviewer_sentiment + 0.20 * evidence_quality`

Evidence quality depends on:
- concrete examples
- clear usage statements
- explicit recommendation language
- consistency of reasoning
- clear pros/cons

## Overall score
Base:
`mean(valid per-video product scores)`

If comments are enabled:
- comment evidence can adjust overall score by at most 7 points up or down.
- comments must never overpower the three reviewer analyses.

## Verdict thresholds
- 80-100: BUY
- 65-79: BUY WITH CAVEATS
- 45-64: MIXED
- 0-44: DO NOT BUY

## Confidence increases when
- reviewers agree
- usage periods are clearly stated
- review evidence is concrete
- comments support reviewer conclusions

## Confidence decreases when
- reviewers disagree strongly
- transcripts are incomplete
- all videos are first impressions
- recommendation is implicit
- comments are noisy
