# Failure Handling

## YouTube search fails
Return a clear upstream error.

## Fewer than 3 relevant videos
Use available valid videos and show a warning.

## Transcript unavailable
Skip candidate and try the next ranked candidate.

## AI quota exhausted
- stop unnecessary further calls
- preserve completed analyses
- identify failed stage clearly

## Invalid model JSON
- perform one repair attempt
- if still invalid, mark that analysis failed
- continue with other videos

## Comments disabled
No comment API or comment-model call should occur.

## Comments unavailable
Continue normal transcript analysis and report:
`Comments unavailable for this video.`

## One video fails
Generate the overall result from remaining successful analyses where possible, with lower confidence.
