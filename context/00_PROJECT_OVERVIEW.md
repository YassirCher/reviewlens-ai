# Project Overview — ReviewLens

ReviewLens is a POC that answers: **"Based on the three most-viewed relevant YouTube reviews, should I buy this product?"**

V1 flow:
1. User enters product name.
2. User optionally enables top-comment analysis (OFF by default).
3. Backend searches YouTube and filters review candidates.
4. Up to 3 high-view candidates with usable transcripts are selected.
5. Each transcript is analyzed independently, with optional comments included in the same AI call.
6. Final consensus compares the structured video analyses.
7. UI shows a 0-100 score, verdict, confidence, usage-period evidence, pros/cons, disagreements and per-source evidence.

The project is intentionally transcript-first rather than multimodal-video-first to keep it cheap and explainable.
