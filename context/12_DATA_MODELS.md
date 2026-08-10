# Data Models

## VideoCandidate
- video_id
- title
- channel
- published_at
- thumbnail_url
- view_count
- duration_seconds
- relevance_score

## TranscriptSegment
- text
- start_seconds
- duration_seconds

## EvidenceItem
- claim
- evidence_text
- timestamp_seconds
- confidence

## CommentAnalysis
- comments_analyzed
- positive_pct
- neutral_pct
- negative_pct
- recurring_pros
- recurring_cons
- repeated_issues
- audience_agrees_with_reviewer
- confidence_score

## VideoAnalysis
Defined in `07_ANALYSIS_SCHEMA.md`.

## OverallAnalysis
- score
- verdict
- confidence
- summary
- consensus_pros
- consensus_cons
- disagreements
- longest_usage_period
- who_should_buy
- who_should_avoid
