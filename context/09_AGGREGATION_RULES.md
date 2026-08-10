# Aggregation Rules

## Inputs
All successful per-video structured analyses, ideally 3.

## Aggregator responsibilities
1. Compare scores.
2. Find consensus pros.
3. Find consensus cons.
4. Expose disagreements.
5. Compare usage periods.
6. Detect long-term evidence.
7. Produce final purchase recommendation.
8. Produce confidence.
9. Explain reasoning briefly.

## Consensus
A finding is strong consensus when at least 2 of 3 reviewers independently mention semantically equivalent feedback.

Example:
- "battery lasts all day"
- "excellent battery endurance"

Combined result:
`Strong battery life — supported by 2/3 reviewers.`

## Long-term evidence
If any reviewer gives a meaningful usage duration, expose it prominently.

Example:
`Strongest long-term evidence: one reviewer used the product for 6 months.`

If all are first impressions:
`Long-term reliability cannot be judged from the selected reviews.`

## Disagreement
Never hide disagreement.

Example:
`Camera quality is disputed: two reviewers liked it, one called it inconsistent.`

## Final output
- overall_score
- verdict
- confidence
- summary
- consensus_pros
- consensus_cons
- disagreements
- longest_usage_period
- who_should_buy
- who_should_avoid
