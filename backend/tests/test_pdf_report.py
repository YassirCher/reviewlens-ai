from unittest.mock import MagicMock
import hmac
import uuid
from app.api.v2 import analyses as routes
from app.db.models import Report, ReportPublication, AnalysisRun
from app.public.reports import token_hash
from app.services.pdf_generator import generate_report_pdf

def test_generate_report_pdf_direct():
    payload = {
        "product_name": "Test Phone Pro",
        "overall_score": 85,
        "verdict": "strong_buy",
        "confidence": 92,
        "confidence_band": "high",
        "source_count_requested": 5,
        "source_count_analyzed": 5,
        "summary": "The Test Phone Pro is an outstanding flagship with excellent features.",
        "longest_usage_period": "6 months",
        "who_should_buy": ["Power users", "Gamers"],
        "who_should_avoid": ["Budget seekers"],
        "limitations": ["Small sample size"],
        "warnings": [],
        "consensus_pros": [
            {"statement": "Great display quality", "source_ids": ["s1"]}
        ],
        "consensus_cons": [
            {"statement": "Slow charging speed", "source_ids": ["s1"]}
        ],
        "disagreements": [
            {"topic": "Audio quality", "side_a": "Loud", "side_b": "Tinny", "side_a_source_ids": ["s1"], "side_b_source_ids": ["s2"]}
        ],
        "sources": [
            {
                "id": "s1",
                "title": "Review 1",
                "channel": "Tech Channel",
                "views": 100000,
                "duration_seconds": 600,
                "usage_period": "3 months",
                "recommendation_summary": "Highly recommended",
            }
        ]
    }
    token = "a" * 43
    pdf_bytes = generate_report_pdf(payload, token)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 2000

def test_download_report_pdf_route():
    token = "b" * 43
    digest = token_hash(token)
    db = MagicMock()
    
    report_id = uuid.uuid4()
    run_id = uuid.uuid4()
    publication = ReportPublication(
        id=uuid.uuid4(),
        report_id=report_id,
        run_id=run_id,
        token_hash=digest,
        payload={"product_name": "Test Phone", "overall_score": 80, "verdict": "buy"},
        graph_payload={"nodes": [], "edges": []},
        content_hash="abc",
    )
    report = Report(
        id=report_id,
        run_id=run_id,
        status="published",
    )
    run = AnalysisRun(
        id=run_id,
        status="complete",
    )

    db.scalar.return_value = publication
    db.get.side_effect = lambda model, pk: report if model is Report else run
    db.execute.return_value.one.return_value = (50000, 10, 0)

    res = routes.download_report_pdf(token, db=db)
    assert res.status_code == 200
    assert res.media_type == "application/pdf"
    assert "ReviewLens-Test-Phone-Dossier.pdf" in res.headers["content-disposition"]
    assert res.body.startswith(b"%PDF")
