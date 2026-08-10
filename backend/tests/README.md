# Minimum Test Plan

1. Comments toggle OFF
   - comment service is never called

2. Missing transcript for first candidate
   - next ranked candidate is tried

3. Only two valid transcripts
   - partial result returned with warning

4. Prompt injection inside transcript
   - treated as data

5. Usage period extraction
   - "I've used this for six months" -> usage_period_mentioned=true

6. Conflicting reviewers
   - final result exposes disagreement

7. Invalid provider JSON
   - one repair attempt, then graceful failure

8. Secrets
   - API response never contains environment keys
