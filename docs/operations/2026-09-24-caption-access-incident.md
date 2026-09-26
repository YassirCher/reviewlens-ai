# Caption access incident: Apple AirPods Pro 2 (2026-09-24)

## Observed failure

The screenshot matches run `e628492b-aaea-413c-9d94-f96767d7cd58`, created at 19:38:36 UTC and failed at 19:39:27 UTC (50.7 seconds from start to terminal state). It requested eight sources with comments enabled. PostgreSQL task attempts and tool invocations show:

| Stage | Recorded result |
|---|---|
| Research plan, YouTube search/details, source curation | Succeeded. YouTube Data API search and video-detail HTTP calls returned 200 in the worker log. |
| `youtube.transcript` | Eight invocations, eight `transcript_access_blocked` upstream failures; each took 6.5–7.5 seconds. |
| Review, product information, comments, consensus, audit, publication | Skipped after caption acquisition failed. No source was analyzed. |
| Model usage | 8,127 tokens: 778 for `plan_research`, 7,349 for `curate_sources`; no downstream model calls. |

Run `1ef44d8b-50d9-4ae0-8b7d-7796b3d4133b` at 19:40 UTC repeated the block on all five caption requests. The running worker reports **no** `YOUTUBE_TRANSCRIPT_PROXY_URL` and **no** `YOUTUBE_COOKIES_PATH`. This is a caption-access problem on the worker's outbound route, not evidence that those videos lack captions or that YouTube search quota is exhausted. The application maps both `IpBlocked` and `RequestBlocked` to `transcript_access_blocked`, so the stored records do not prove the precise HTTP response or which of those two exceptions occurred.

The current [transcript provider](../../backend/app/tools/youtube.py) catches those block exceptions, waits 1.5 then 3 seconds, and retries on the **same** chosen route. [Transcript task execution](../../backend/app/analysis/executor.py) correctly stops trying substitute videos for that slot on `transcript_access_blocked`. The [public API](../../backend/app/api/v2/analyses.py) correctly displays the caption-access error when no review succeeded. These classification changes are already present in the dirty worktree; preserve them.

## Exact correction

1. Supply a working outbound route for **caption requests from the worker**. Use a verified HTTP(S) proxy endpoint whose exit IP rotates or otherwise remains usable for YouTube captions. The existing `GenericProxyConfig` support reads `YOUTUBE_TRANSCRIPT_PROXY_URL`; it chooses one endpoint per `TranscriptProvider` instance. A comma-separated list is supported, but random selection can still pick a blocked endpoint, and the retries do not select a different entry. Start with one verified rotating endpoint. Keep its credentials in the active private Compose environment file (`REVIEWLENS_ENV_FILE`, or `.env` when that variable is unset), never in source control or a ticket. URL-encode reserved characters in credentials. Leave `YOUTUBE_COOKIES_PATH` unset for this repair: the [upstream library says cookie authentication is currently unavailable](https://github.com/jdepoix/youtube-transcript-api/blob/master/README.md#cookie-authentication).
2. Recreate only the worker so it reads the new environment: `docker compose up -d --no-deps --force-recreate worker`. Use the same `REVIEWLENS_ENV_FILE` setting for this command as for the running stack. A proxy URL setting needs no code rebuild. Confirm the worker's setting without printing the URL: `docker compose exec -T worker python -c 'from app.config import settings; print(bool(settings.youtube_transcript_proxy_url))'` must print `True`.
3. From that worker, make **one caption-only probe** against a video whose transcript was previously acquired successfully (`qr_n9Tam1jc`). This performs no OpenRouter call and prints neither transcript text nor proxy credentials. In PowerShell:

   ```powershell
   @'
   from time import perf_counter
   from app.config import settings
   from app.tools.contracts import YouTubeTranscriptInput
   from app.tools.youtube import TranscriptProvider
   start = perf_counter()
   result = TranscriptProvider(config=settings).fetch(YouTubeTranscriptInput(video_id="qr_n9Tam1jc"))
   print({"segments": len(result.segments), "seconds": round(perf_counter() - start, 2)})
   '@ | docker compose exec -T worker python -
   ```

   Require a positive segment count. If it still raises `transcript_access_blocked`, verify that the endpoint actually rotates to an unblocked exit and that the worker can reach it; replace the route before starting another research run. Do not repeatedly retry the same blocked IP.
4. Run one eight-source, comments-enabled research request with the same workflow/model policy after the probe succeeds. Verify at least one `youtube.transcript` invocation succeeds and at least one `analyze_review.source_*` task produces an analysis. Confirm a complete or explicitly partial report has valid evidence and reports the actual requested-versus-analyzed coverage. If it fails, inspect `tool_invocations.error_code` and the worker logs before further attempts.

The YouTube Data API key and quota settings do not repair this path: search/details succeeded, while caption retrieval uses `youtube-transcript-api`. The official YouTube `captions.download` method requires OAuth authorization and can return 403 when the caller lacks track permissions, so it is not a drop-in replacement for arbitrary review videos. See the [library's proxy guidance](https://github.com/jdepoix/youtube-transcript-api/blob/master/README.md#working-around-ip-bans-requestblocked-or-ipblocked-exception) and [YouTube caption download contract](https://developers.google.com/youtube/v3/docs/captions/download).

## Latency and token guardrails

Keep the same source count, comments setting, workflow version, model policies, prompts, context budgets, and task fanout. The proxy changes only how the existing caption HTTP requests leave the worker; it adds **zero model calls** and no transcript text to prompts beyond what a successful run already uses. A nearby proxy exit and a successful first attempt should avoid the 4.5 seconds of blind sleep now paid on each blocked caption invocation. Record caption fetch durations and full run duration after rollout; a proxy can add network time, so identical latency cannot be promised without measurement.

The screenshot's 50.7 seconds and 8,127 tokens are a **failed-run** baseline. A successful report necessarily executes the analysis, consensus, and audit calls that this run skipped; it cannot both produce that report and stay at 8,127 total tokens. Two earlier eight-source, comments-enabled partial runs took 125.1 and 160.1 seconds and used 146,823 and 149,159 tokens. They are reference observations, not guaranteed limits. Compare the repaired run with comparable successful/partial runs and the unchanged configured run budgets; investigate any new call count or prompt-budget increase. Do not add an LLM transcript fallback, audio transcription, a larger candidate pool, or extra planning calls to fix this network error.

## Follow-up code hardening

In [youtube.py](../../backend/app/tools/youtube.py), retry `IpBlocked`/`RequestBlocked` only if the retry uses a **different verified egress IP** and remains inside the existing transcript timeout. Otherwise fail on the first block instead of sleeping 4.5 seconds on the same route. Preserve `transcript_access_blocked` as distinct from `transcript_unavailable`; never mark a blocked video as captionless. Add a mocked test for a blocked static route (one attempt, prompt failure) and a rotating route (bounded alternate attempt), plus a test that no extra OpenRouter usage event is created. If several source slots share one fixed blocked route, a short lived route-specific circuit breaker can prevent all eight from repeating the same failure; it must not suppress healthy alternate routes. Verify failure latency decreases and successful caption latency stays within the existing baseline before enabling this hardening.

No code or production configuration was changed during this investigation. The incident evidence came from the local Compose worker logs and PostgreSQL `analysis_runs`, `task_attempts`, `tool_invocations`, and `usage_events` records on 2026-09-24.
