# Per-Video Analysis Schema

## Video identity
- video_id
- title
- channel
- url
- view_count

## Review context
- review_type:
  - first_impressions
  - short_term
  - long_term
  - comparison
  - unknown

- usage_period_mentioned: boolean
- usage_period_raw: string or null
- usage_period_days_estimate: number or null
- ownership_context:
  - owned
  - loaned
  - review_unit
  - unknown

## Scores, all 0-100
- product_score
- reviewer_sentiment_score
- purchase_recommendation_score
- confidence_score

## Purchase decision
- purchase_verdict:
  - buy
  - buy_with_caveats
  - mixed
  - do_not_buy
  - unclear

- recommendation_summary

## Findings
- pros[]
- cons[]
- major_issues[]
- recommended_for[]
- not_recommended_for[]

## Evidence
Each evidence item:
- claim
- evidence_text
- timestamp_seconds
- confidence

## Optional comment result
- comments_analyzed
- positive_pct
- neutral_pct
- negative_pct
- recurring_pros[]
- recurring_cons[]
- repeated_issues[]
- audience_agrees_with_reviewer
- confidence_score
