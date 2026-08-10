from app.config import settings
from app.models import CommentItem, TranscriptSegment

PRIORITY_TERMS = (
    "recommend", "buy", "bought", "purchase", "verdict", "conclusion",
    "after", "using", "used", "month", "months", "week", "weeks",
    "day", "days", "year", "years", "problem", "issue", "issues",
    "good", "bad", "pros", "cons", "worth", "avoid", "return",
)


def ts(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def compact_transcript(segments: list[TranscriptSegment]) -> str:
    lines = [f"[{ts(s.start_seconds)}] {s.text}" for s in segments]
    joined = "\n".join(lines)
    limit = settings.max_transcript_chars_per_video
    if len(joined) <= limit:
        return joined

    # Preserve beginning/end plus high-signal segments from the middle without
    # requiring additional summarization LLM calls.
    head_budget = int(limit * 0.22)
    tail_budget = int(limit * 0.28)
    signal_budget = limit - head_budget - tail_budget - 500

    head: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > head_budget:
            break
        head.append(line)
        used += len(line) + 1

    tail: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > tail_budget:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()

    selected = set(head) | set(tail)
    signals: list[str] = []
    used = 0
    for line in lines:
        lower = line.lower()
        if line in selected or not any(term in lower for term in PRIORITY_TERMS):
            continue
        if used + len(line) + 1 > signal_budget:
            break
        signals.append(line)
        used += len(line) + 1

    return (
        "--- BEGINNING ---\n" + "\n".join(head)
        + "\n\n--- HIGH-SIGNAL EXCERPTS ---\n" + "\n".join(signals)
        + "\n\n--- ENDING ---\n" + "\n".join(tail)
    )[:limit]


def compact_comments(comments: list[CommentItem]) -> str:
    if not comments:
        return ""
    # Sort by likes but keep relevance order as a secondary property.
    ranked = sorted(enumerate(comments), key=lambda pair: (pair[1].like_count, -pair[0]), reverse=True)
    lines = [f"- [likes={comment.like_count}] {comment.text}" for _, comment in ranked]
    output: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > settings.max_comment_chars_per_video:
            break
        output.append(line)
        used += len(line) + 1
    return "\n".join(output)
