# Optional Comment Analysis

Comments are OFF by default and explicitly enabled by the user.

When enabled:
1. Retrieve up to 15 top/relevant comments per selected video.
2. Remove empty duplicates.
3. Include the comments in the **same per-video AI request as the transcript**.
4. Return audience sentiment, recurring positives/negatives, repeated issues and whether the audience broadly agrees with the reviewer.

This design keeps the normal LLM call budget at approximately four calls whether comments are enabled or not.

Comments are secondary evidence. They must not outweigh the reviewer and isolated complaints must not be treated as recurring defects.
