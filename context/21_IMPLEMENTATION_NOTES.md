# Implementation Notes

## YouTube discovery
Uses `search.list` with:
- query `<product> review`
- `type=video`
- `order=viewCount`
- `videoCaption=closedCaption`

Then `videos.list` loads statistics/content details for filtering.

A second `long term review` search is used only when the first result pool is small, protecting YouTube quota.

## Transcript extraction
Uses the current `YouTubeTranscriptApi().fetch(video_id, languages=[...])` API. If preferred languages fail, the first available transcript is attempted and translated to English when possible.

## Structured AI
Providers receive Pydantic JSON Schema through `response_format.type=json_schema`. If a selected free/legacy model rejects schema mode, the adapter makes one compatibility fallback to JSON-object mode and still validates locally with Pydantic.

## Streaming UI
`POST /api/analyze/stream` returns SSE events:
- `progress`
- `video_result`
- `result`
- `error`

The current UI uses progress and final result; the per-video event is ready for future progressive card rendering.
