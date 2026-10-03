from unittest.mock import MagicMock
import uuid
from app.api.v2 import analyses as routes
from app.db.models import Report, ReportPublication, AnalysisRun
from app.public.reports import token_hash
from app.services.pdf_generator import _claim_citations, _evidence_links, generate_report_pdf

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


def test_pdf_accepts_product_details_and_unconfirmed_sample():
    video_id = "abc123DEF45"
    evidence = {
        "video_id": video_id,
        "source_url": f"https://www.youtube.com/watch?v={video_id}&t=12s",
        "source_part": "transcript",
        "excerpt": "The tested unit has a 5000 mAh battery.",
        "timestamp_seconds": 12,
    }
    payload = {
        "product_name": "Test device",
        "summary": "A source-grounded report.",
        "product_info": {"facts": [{"group": "Power", "label": "Battery", "value": "5000 mAh", "scope": None,
                                    "evidence": [evidence], "conflicting": False}], "variants": []},
        "sources": [{"id": "s1", "title": "Review", "channel": "Reviewer", "video_id": video_id,
                     "recommendation_summary": "A qualified recommendation.", "sample_used": {"units": []}}],
    }
    data = generate_report_pdf(payload, "a" * 43)
    assert data.startswith(b"%PDF") and len(data) > 2000


def test_pdf_product_links_use_report_source_numbers() -> None:
    first = "abc123DEF45"
    second = "def456GHI78"
    refs = [
        {"video_id": second, "source_url": f"https://www.youtube.com/watch?v={second}&t=42s",
         "source_part": "transcript", "excerpt": "The battery lasts 30 hours.", "timestamp_seconds": 42},
        {"video_id": first, "source_url": f"https://www.youtube.com/watch?v={first}",
         "source_part": "description", "excerpt": "Available in black", "timestamp_seconds": None},
    ]
    links = _evidence_links(refs, {first: 1, second: 2})
    assert "Review source 2" in links and "Review source 1" in links
    assert links.index("Review source 2") < links.index("Review source 1")
    assert f"watch?v={second}&amp;t=42s" in links


def test_pdf_retains_all_public_claim_excerpts_and_safe_timestamp_links() -> None:
    source = {'video_id': 'abc123DEF45', 'claims': [{'evidence': [
        {'text': 'Measured outside noise fell by 84%.', 'timestamp_start_seconds': 124.56},
        {'text': 'Bluetooth version is 5.2.', 'timestamp_start_seconds': 272},
        {'text': 'The headphones do not fold.', 'timestamp_start_seconds': 320},
    ]}]}
    citations = _claim_citations(source)
    assert len(citations) == 3 and '84%' in citations[0][0]
    assert '&amp;t=124s' in citations[0][1] and '2:04' in citations[0][1]
    assert '&amp;t=320s' in citations[2][1]
    assert _claim_citations({**source, 'video_id': 'javascript:bad'}) == []


def test_pdf_guidance_bullets_and_missing_duration_are_readable(monkeypatch) -> None:
    import app.services.pdf_generator as generator
    original = generator.Paragraph
    paragraphs = []

    def capture(text, *args, **kwargs):
        paragraphs.append(text)
        return original(text, *args, **kwargs)

    monkeypatch.setattr(generator, 'Paragraph', capture)
    payload = {'product_name': 'Sony WH-1000XM5', 'summary': 'A partial report.',
        'warnings': ['partial_source_coverage'],
        'sources': [{'id': 's1', 'channel': 'Reviewer', 'title': 'Review', 'video_id': 'abc123DEF45',
            'usage_period': None, 'claims': [{'evidence': [{'text': 'The headphones do not fold.',
                                                        'timestamp_start_seconds': 320}]}]}],
        'decision_guide': {'unknowns': ['Current price']}}
    assert generate_report_pdf(payload, 'a' * 43).startswith(b'%PDF')
    text = '\n'.join(paragraphs)
    assert 'â€¢' not in text and '&bull; Current price' in text
    assert 'Stated testing period' not in text and 'Not established' in text
    assert 'Some requested sources could not be analyzed.' in text
    assert 'The headphones do not fold.' in text and '5:20 in original review' in text


def test_pdf_includes_optional_decision_guide() -> None:
    payload = {
        "product_name": "Test headphones", "summary": "Cited review summary.",
        "consensus_pros": [{"id": "finding-1", "statement": "Comfort lasted two hours", "source_ids": ["source-1"]}],
        "consensus_cons": [],
        "sources": [{"id": "source-1", "channel": "Reviewer", "title": "Review", "video_id": "abc123DEF45",
                     "sample_used": {"units": [{"role": "Review unit", "details": [
                         {"label": "Color", "value": "black", "evidence": {
                             "video_id": "abc123DEF45",
                             "source_url": "https://www.youtube.com/watch?v=abc123DEF45",
                             "source_part": "description", "excerpt": "Review unit is black",
                             "timestamp_seconds": None,
                         }},
                     ]}]}}],
        "decision_guide": {
            "buy_if_finding_ids": ["finding-1"], "caveat_finding_ids": [],
            "tested_source_ids": ["source-1"], "long_term_period": "two months",
            "long_term_source_id": "source-1", "unknowns": ["Current warranty terms"],
        },
    }
    data = generate_report_pdf(payload, "a" * 43)
    assert data.startswith(b"%PDF") and len(data) > 2000

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
