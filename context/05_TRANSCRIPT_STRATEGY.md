# Transcript Strategy

## Primary approach
Analyze transcript/caption text instead of full video frames or audio.

## Keep timestamps
Normalize transcript into segments:
- text
- start_seconds
- duration_seconds

This allows the UI to cite where a claim came from.

## Long transcripts
Do not blindly send unlimited transcript text.

V1 strategy:
1. Build timestamped transcript.
2. Apply configured character/token cap.
3. If too long, chunk it.
4. Extract structured facts per chunk.
5. Merge chunk findings into one video analysis.

## Missing transcript
When a candidate has no usable transcript:
1. Skip it.
2. Try the next ranked candidate.
3. Continue until 3 videos are found or candidates are exhausted.

If fewer than 3 are available, return partial results with a warning.

## Evidence rule
Important claims should include:
- claim
- short evidence text
- timestamp if available
- confidence
