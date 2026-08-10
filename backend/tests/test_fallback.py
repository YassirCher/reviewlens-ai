from app.models import VideoAnalysis, VideoCandidate
from app.services.ai_service import AIService


def make_analysis(score: int) -> VideoAnalysis:
    return VideoAnalysis(
        video=VideoCandidate(video_id=str(score), title=f"Review {score}", channel="Test", url="https://youtube.com", view_count=score),
        product_score=score,
        reviewer_sentiment_score=score,
        purchase_recommendation_score=score,
        confidence_score=80,
        purchase_verdict="buy" if score >= 80 else "mixed",
        recommendation_summary="Test",
    )


def test_deterministic_consensus():
    result = AIService.deterministic_fallback([make_analysis(80), make_analysis(85), make_analysis(90)])
    assert result.score == 85
    assert result.verdict == "buy"
