# Video Selection Strategy

## Goal
Choose the 3 most-viewed relevant review videos, not simply the 3 most-viewed videos containing the product name.

## Main query
`<product name> review`

Fallback query variants:
- `<product name> test`
- `<product name> long term review`
- `<product name> after using`

## Candidate pool
Default: 12 candidate videos.

## Exclude or heavily penalize
- Shorts
- livestreams
- advertisements
- official launch videos with no real review
- videos clearly focused on another product
- irrelevant compilations
- candidates without usable transcript when alternatives exist

## Ranking
1. Check product-title relevance.
2. Check whether title/content looks like a review/test/comparison.
3. Remove obvious invalid results.
4. Sort remaining valid candidates primarily by view count.
5. Try candidates in order until 3 usable transcripts are obtained.

## Important
A highly viewed launch trailer must not outrank a real review just because it has more views.
