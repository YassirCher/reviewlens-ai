# User Flow

## Search page

Fields:
- Product name
- `Analyze top comments` toggle, OFF by default
- Analyze button

Example:
- Product: `POCO F7`
- Analyze top comments: OFF

## Loading stages
1. Searching YouTube
2. Filtering review videos
3. Fetching transcripts
4. Analyzing video 1/3
5. Analyzing video 2/3
6. Analyzing video 3/3
7. Analyzing comments, only if enabled
8. Building final verdict

## Overall result
Show:
- Product name
- Overall score 0-100
- Verdict
- Confidence 0-100
- Short explanation
- Consensus pros
- Consensus cons
- Reviewer disagreements
- Best long-term usage evidence
- Who should buy
- Who should avoid

## Per-video card
Show:
- Thumbnail
- Title
- Channel
- Views
- URL
- Video score
- Reviewer sentiment
- Purchase recommendation
- Usage period mentioned: yes/no
- Usage period value
- Review type
- Pros
- Cons
- Major issues
- Evidence snippets and timestamps
- Optional comment-analysis section
