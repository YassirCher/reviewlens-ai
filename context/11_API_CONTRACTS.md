# API Contracts

## GET /api/config

Returns configured provider availability without exposing keys.

## POST /api/analyze

Request:
```json
{
  "product_name": "POCO F7",
  "analyze_comments": false,
  "provider": "auto"
}
```

`provider` can be `auto`, `openrouter`, `xai`, or `openai`.

Response contains:
- analysis ID,
- product name,
- provider/model used,
- per-video analyses,
- overall consensus,
- warnings.

## POST /api/analyze/stream

Same request body, returned as Server-Sent Events.

Events:
- `progress` — stage, label, percentage, detail
- `video_result` — completed source analysis
- `result` — final `AnalyzeResponse`
- `error` — terminal error message

The frontend uses this endpoint for live progress.
