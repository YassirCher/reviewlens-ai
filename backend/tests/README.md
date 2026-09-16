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

9. Durable runtime
   - run, immutable snapshot, DAG tasks, budget state, first event, and outbox commit atomically
   - duplicate Celery delivery creates one successful attempt/effect
   - automatic retry appends attempts without overwriting history
   - stale worker leases resume from PostgreSQL

10. Cancellation and progress
   - cancellation prevents downstream dispatch
   - Redis Streams resume after an event sequence
   - missing/expired Redis history falls back to PostgreSQL
   - progress is never delivered before its PostgreSQL event commits

11. Context graph and retrieval
   - validated Markdown/frontmatter writes are atomic and path-confined
   - node versions/manifests are immutable and relations are type-checked
   - file/database drift is quarantined or marked degraded without partial authority
   - Neo4j and Qdrant rebuild from PostgreSQL/Markdown using recorded hashes and policy versions
   - deterministic manifests retain provenance and never silently truncate required evidence
   - PostgreSQL graph/lexical retrieval continues truthfully when either projection is unavailable

12. Typed research tools
   - exactly the published `1.0.0` static registry is executable; database metadata cannot select handlers or URLs
   - task/agent snapshot allowlists, roles, workspaces, timeouts, retries, and output caps are enforced
   - every external attempt has a sanitized durable invocation and Pacific-window quota reservation
   - source selection is relevant, diverse, deterministic, and continues past missing transcripts
   - comments are opt-in, bounded, deduplicated, and secondary-trust only
   - transcript chunks preserve timestamp order and source lineage
   - injected source text remains untrusted data; evidence and scoring reject unsupported publication
