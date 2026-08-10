from app.services.youtube_service import parse_iso8601_duration, relevance_score


def test_duration_parser():
    assert parse_iso8601_duration("PT12M5S") == 725
    assert parse_iso8601_duration("PT1H2M3S") == 3723


def test_review_title_scores_higher_than_trailer():
    review = relevance_score("POCO F7", "POCO F7 Review after 30 days")
    trailer = relevance_score("POCO F7", "POCO F7 Official Launch Trailer")
    assert review > trailer
