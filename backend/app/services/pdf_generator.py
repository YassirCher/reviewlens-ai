"""
ReviewLens High-Fidelity Executive PDF Report Generator
Uses ReportLab Platypus to generate publication-grade multi-page research dossiers.
"""
from __future__ import annotations

import io
import html
import math
import re
from typing import Any
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
    HRFlowable,
)
from reportlab.pdfgen import canvas

from app.analysis.product_info import ProductEvidence, ProductInfo, SampleUsed

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and draw running headers and 'Page X of Y' footers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int) -> None:
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748B"))

        # Running header on page 2+
        if self._pageNumber > 1:
            product_title = getattr(self, "product_title", "ReviewLens Research Report")
            self.drawString(54, 11 * inch - 36, f"REVIEWLENS RESEARCH DOSSIER - {product_title.upper()}")
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.setLineWidth(0.5)
            self.line(54, 11 * inch - 42, 8.5 * inch - 54, 11 * inch - 42)

        # Running footer on all pages
        footer_text = f"ReviewLens AI - Verified Grounded Intelligence - Page {self._pageNumber} of {page_count}"
        self.drawRightString(8.5 * inch - 54, 30, footer_text)
        self.drawString(54, 30, "CONFIDENTIAL & INDEPENDENT CONSUMER RESEARCH")
        self.setStrokeColor(colors.HexColor("#CBD5E1"))
        self.setLineWidth(0.5)
        self.line(54, 42, 8.5 * inch - 54, 42)

        self.restoreState()


def _sanitize(val: Any) -> str:
    if val is None:
        return ""
    text = str(val)
    replacements = {
        "\u2014": " -- ",
        "\u2013": " - ",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "&bull;",
        "\u00b7": "&bull;",
        "\u2026": "...",
        "\u2713": "[+]",
        "\u2714": "[+]",
        "\u2715": "[-]",
        "\u2716": "[-]",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    cleaned = []
    for ch in text:
        if ord(ch) < 256 and ord(ch) != 127:
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return html.escape("".join(cleaned))


def _verdict_label(val: str) -> str:
    return val.replace("_", " ").title()


def _evidence_links(raw: list[dict[str, Any]] | tuple[ProductEvidence, ...]) -> str:
    links = []
    for index, value in enumerate(raw):
        try:
            ref = value if isinstance(value, ProductEvidence) else ProductEvidence.model_validate(value)
        except ValueError:
            continue
        if not _VIDEO_ID.fullmatch(ref.video_id):
            continue
        url = f"https://www.youtube.com/watch?v={ref.video_id}"
        if ref.timestamp_seconds is not None and math.isfinite(ref.timestamp_seconds) and ref.timestamp_seconds >= 0:
            url += f"&amp;t={int(ref.timestamp_seconds)}s"
        links.append(f'<link href="{url}" color="#0284C7">Review source {index + 1}</link>')
    return ", ".join(links)


def generate_report_pdf(payload: dict[str, Any], token: str) -> bytes:
    """Generates an executive, publication-grade PDF dossier from a report payload."""
    buffer = io.BytesIO()

    # Document geometry: Letter, 0.75 in (54 pt) margins
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=50,
        bottomMargin=54,
    )

    product_name = payload.get("product_name", "Product Analysis")
    styles = getSampleStyleSheet()

    # Custom typography styles
    c_dark = colors.HexColor("#0F172A")
    c_muted = colors.HexColor("#64748B")
    c_primary = colors.HexColor("#0284C7")
    c_emerald = colors.HexColor("#059669")
    c_amber = colors.HexColor("#D97706")
    c_rose = colors.HexColor("#E11D48")
    c_bg_light = colors.HexColor("#F8FAFC")
    c_border = colors.HexColor("#E2E8F0")

    style_kicker = ParagraphStyle(
        "Kicker",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=c_primary,
        textTransform="uppercase",
        spaceAfter=4,
    )
    style_title = ParagraphStyle(
        "DocTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=24,
        leading=28,
        textColor=c_dark,
        alignment=0,
        spaceAfter=12,
    )
    style_sec_heading = ParagraphStyle(
        "SecHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=c_dark,
        spaceBefore=12,
        spaceAfter=6,
    )
    style_body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#1E293B"),
        spaceAfter=6,
    )
    style_body_bold = ParagraphStyle(
        "BodyBold",
        parent=style_body,
        fontName="Helvetica-Bold",
    )
    style_quote = ParagraphStyle(
        "Quote",
        parent=styles["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155"),
        spaceAfter=4,
    )
    style_meta = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=c_muted,
    )
    style_pill_bold = ParagraphStyle(
        "PillBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=c_dark,
        alignment=1,
    )
    style_pill_sub = ParagraphStyle(
        "PillSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9,
        textColor=c_muted,
        alignment=1,
    )

    story: list[Any] = []

    # -------------------------------------------------------------
    # 1. HEADER & EXECUTIVE TITLE
    # -------------------------------------------------------------
    story.append(Paragraph("REVIEWLENS RESEARCH DOSSIER &bull; GROUNDED AI INTELLIGENCE", style_kicker))
    story.append(Paragraph(_sanitize(product_name), style_title))

    # Executive Score & Metadata Table
    overall_score = payload.get("overall_score", 0)
    verdict_raw = payload.get("verdict", "undetermined")
    verdict_str = _verdict_label(verdict_raw)
    confidence = payload.get("confidence", 0)
    confidence_band = _verdict_label(payload.get("confidence_band", "normal"))
    sources_analyzed = payload.get("source_count_analyzed", 0)
    sources_requested = payload.get("source_count_requested", 0)
    longest_usage = payload.get("longest_usage_period", "Not established")
    gen_at = payload.get("generated_at", "")
    try:
        gen_date = datetime.fromisoformat(gen_at.replace("Z", "+00:00")).strftime("%b %d, %Y")
    except Exception:
        gen_date = gen_at[:10] if gen_at else "Recent"

    score_cell = [
        Paragraph(f"<font size=18 color='#0284C7'><b>{overall_score}</b></font><font size=9 color='#64748B'>/100</font>", ParagraphStyle("ScoreValue", parent=style_pill_bold, leading=23)),
        Spacer(1, 3),
        Paragraph(f"<b>{verdict_str}</b>", ParagraphStyle("PillTag", parent=style_pill_sub, fontName="Helvetica-Bold", textColor=c_primary)),
    ]

    metrics_table_data = [
        [
            score_cell,
            [Paragraph("<b>CONFIDENCE</b>", style_pill_sub), Paragraph(f"<b>{confidence}%</b>", style_pill_bold), Paragraph(confidence_band, style_pill_sub)],
            [Paragraph("<b>EVIDENCE SCOPE</b>", style_pill_sub), Paragraph(f"<b>{sources_analyzed} / {sources_requested}</b>", style_pill_bold), Paragraph("Source Reviews", style_pill_sub)],
            [Paragraph("<b>LONGEST USAGE</b>", style_pill_sub), Paragraph(f"<b>{longest_usage}</b>", style_pill_bold), Paragraph("Tested Period", style_pill_sub)],
            [Paragraph("<b>REPORT DATE</b>", style_pill_sub), Paragraph(f"<b>{gen_date}</b>", style_pill_bold), Paragraph(f"ID: {token[:8]}...", style_pill_sub)],
        ]
    ]
    col_w = (8.5 * inch - 108) / 5
    metrics_table = Table(metrics_table_data, colWidths=[col_w] * 5)
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c_bg_light),
        ("BOX", (0, 0), (-1, -1), 1, c_border),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, c_border),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(metrics_table)
    story.append(Spacer(1, 14))

    # -------------------------------------------------------------
    # 2. EXECUTIVE SUMMARY
    # -------------------------------------------------------------
    summary_text = payload.get("summary", "No executive summary available.")
    summary_box_data = [
        [
            Paragraph("<b>EXECUTIVE SUMMARY &amp; PURCHASING VERDICT</b>", ParagraphStyle("SumKicker", parent=style_kicker, textColor=c_dark)),
        ],
        [
            Paragraph(_sanitize(summary_text), style_body),
        ]
    ]
    summary_table = Table(summary_box_data, colWidths=[8.5 * inch - 108])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F0F9FF")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#BAE6FD")),
        ("LINEBEFORE", (0, 0), (0, -1), 3.5, c_primary),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 14))

    raw_product_info = payload.get("product_info")
    if raw_product_info:
        info = ProductInfo.model_validate(raw_product_info)
        if info.facts or info.variants:
            story.append(Paragraph("PRODUCT DETAILS FROM REVIEWS", style_sec_heading))
            story.append(Paragraph(_sanitize(info.coverage_note), style_meta))
            groups: dict[str, list] = {}
            for fact in info.facts:
                groups.setdefault(fact.group, []).append(fact)
            for group, facts in groups.items():
                story.append(Paragraph(_sanitize(group), style_body_bold))
                for fact in facts:
                    scope = f" ({_sanitize(fact.scope)})" if fact.scope else ""
                    conflict = " - conflicting review statements" if fact.conflicting else ""
                    links = _evidence_links(fact.evidence)
                    story.append(Paragraph(
                        f"<b>{_sanitize(fact.label)}:</b> {_sanitize(fact.value)}{scope}{conflict}"
                        + (f" - {links}" if links else ""), style_body,
                    ))
            if info.variants:
                story.append(Paragraph("Options mentioned in reviews", style_body_bold))
                story.append(Paragraph("Listed options do not imply every combination or current availability.", style_meta))
                for option in info.variants:
                    scope = f" ({_sanitize(option.scope)})" if option.scope else ""
                    links = _evidence_links(option.evidence)
                    story.append(Paragraph(
                        f"<b>{_sanitize(option.dimension)}:</b> {_sanitize(option.value)}{scope}"
                        + (f" - {links}" if links else ""), style_body,
                    ))
            story.append(Spacer(1, 12))

    # -------------------------------------------------------------
    # 3. METHODOLOGICAL CONTEXT & REVIEWER BIAS NOTES
    # -------------------------------------------------------------
    limitations = payload.get("limitations", [])
    warnings = payload.get("warnings", [])
    all_notes = warnings + limitations
    if all_notes:
        notes_story: list[Any] = [
            Paragraph("<b>IMPORTANT METHODOLOGICAL CONTEXT &amp; TESTING CONDITIONS</b>", ParagraphStyle("NoteH", parent=style_kicker, textColor=colors.HexColor("#92400E"))),
        ]
        # Bullet list formatted
        for n in all_notes[:8]:  # Top 8 contextual considerations
            notes_story.append(Paragraph(f"• {_sanitize(n)}", ParagraphStyle("NoteItem", parent=style_body, fontSize=8, leading=11, textColor=colors.HexColor("#78350F"))))
        if len(all_notes) > 8:
            notes_story.append(Paragraph(f"<i>...and {len(all_notes) - 8} additional contextual caveats noted in primary sources.</i>", ParagraphStyle("NoteMore", parent=style_meta, textColor=colors.HexColor("#92400E"))))

        notes_table = Table([[notes_story]], colWidths=[8.5 * inch - 108])
        notes_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FEFCE8")),
            ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#FEF08A")),
            ("LINEBEFORE", (0, 0), (0, -1), 3, c_amber),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(notes_table)
        story.append(Spacer(1, 14))

    # Helper maps for sources
    sources_list = payload.get("sources", [])
    source_map = {s.get("id") or s.get("source_id"): s for s in sources_list if s.get("id") or s.get("source_id")}

    # -------------------------------------------------------------
    # 4. MARKET CONSENSUS: STRENGTHS (PROS)
    # -------------------------------------------------------------
    pros = payload.get("consensus_pros", [])
    if pros:
        story.append(Paragraph("1. CORROBORATED STRENGTHS (CONSENSUS PROS)", style_sec_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=c_border, spaceBefore=2, spaceAfter=8))

        pro_elements: list[Any] = []
        for i, pro in enumerate(pros, 1):
            stmt = pro.get("statement", "")
            src_ids = pro.get("source_ids", [])
            src_names = [source_map[sid].get("channel", "Reviewer") for sid in src_ids if sid in source_map]
            src_label = f"Corroborated by {len(src_names)} sources ({', '.join(src_names[:3])})" if src_names else f"Cited by {len(src_ids)} independent sources"

            row_data = [
                [Paragraph(f"<b><font color='#059669'>PRO {i:02d}</font> · {_sanitize(stmt)}</b>", style_body_bold)],
                [Paragraph(f"<i>{src_label}</i>", style_meta)]
            ]
            t = Table(row_data, colWidths=[8.5 * inch - 108])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), c_bg_light),
                ("BOX", (0, 0), (-1, -1), 0.5, c_border),
                ("LINEBEFORE", (0, 0), (0, -1), 3, c_emerald),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            pro_elements.append(t)
            pro_elements.append(Spacer(1, 4))
        story.append(KeepTogether(pro_elements[:4]))  # Keep first batch together
        for elem in pro_elements[4:]:
            story.append(elem)
        story.append(Spacer(1, 10))

    # -------------------------------------------------------------
    # 5. MARKET CONSENSUS: DRAWBACKS & CAVEATS (CONS)
    # -------------------------------------------------------------
    cons = payload.get("consensus_cons", [])
    if cons:
        story.append(Paragraph("2. VERIFIED DRAWBACKS &amp; CAVEATS (CONSENSUS CONS)", style_sec_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=c_border, spaceBefore=2, spaceAfter=8))

        con_elements: list[Any] = []
        for i, con in enumerate(cons, 1):
            stmt = con.get("statement", "")
            src_ids = con.get("source_ids", [])
            src_names = [source_map[sid].get("channel", "Reviewer") for sid in src_ids if sid in source_map]
            src_label = f"Corroborated by {len(src_names)} sources ({', '.join(src_names[:3])})" if src_names else f"Cited by {len(src_ids)} independent sources"

            row_data = [
                [Paragraph(f"<b><font color='#D97706'>CON {i:02d}</font> · {_sanitize(stmt)}</b>", style_body_bold)],
                [Paragraph(f"<i>{src_label}</i>", style_meta)]
            ]
            t = Table(row_data, colWidths=[8.5 * inch - 108])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), c_bg_light),
                ("BOX", (0, 0), (-1, -1), 0.5, c_border),
                ("LINEBEFORE", (0, 0), (0, -1), 3, c_amber),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            con_elements.append(t)
            con_elements.append(Spacer(1, 4))
        story.append(KeepTogether(con_elements[:4]))
        for elem in con_elements[4:]:
            story.append(elem)
        story.append(Spacer(1, 10))

    # -------------------------------------------------------------
    # 6. REVIEWER COLLISIONS & DISAGREEMENTS
    # -------------------------------------------------------------
    disagreements = payload.get("disagreements", [])
    if disagreements:
        story.append(Paragraph("3. CRITICAL DISAGREEMENTS BETWEEN REVIEWERS", style_sec_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=c_border, spaceBefore=2, spaceAfter=8))
        story.append(Paragraph("<i>Where top technical reviewers directly contradicted each other on real-world performance:</i>", style_meta))
        story.append(Spacer(1, 4))

        dis_elements: list[Any] = []
        half_w = (8.5 * inch - 108 - 8) / 2
        for d in disagreements:
            topic = d.get("topic", "Controversy")
            side_a = d.get("side_a", "")
            side_b = d.get("side_b", "")
            a_names = [source_map[sid].get("channel", "Reviewer") for sid in d.get("side_a_source_ids", []) if sid in source_map]
            b_names = [source_map[sid].get("channel", "Reviewer") for sid in d.get("side_b_source_ids", []) if sid in source_map]

            table_data = [
                [
                    Paragraph(f"<b>TOPIC: {_sanitize(topic)}</b>", ParagraphStyle("TopH", parent=style_body_bold, textColor=c_dark)),
                    "",
                ],
                [
                    [
                        Paragraph(f"<b>PERSPECTIVE A</b> ({', '.join(a_names) or 'Reviewers'})", ParagraphStyle("SideATitle", parent=style_meta, fontName="Helvetica-Bold", textColor=c_primary)),
                        Paragraph(f"“{_sanitize(side_a)}”", style_quote),
                    ],
                    [
                        Paragraph(f"<b>PERSPECTIVE B</b> ({', '.join(b_names) or 'Reviewers'})", ParagraphStyle("SideBTitle", parent=style_meta, fontName="Helvetica-Bold", textColor=c_rose)),
                        Paragraph(f"“{_sanitize(side_b)}”", style_quote),
                    ]
                ]
            ]
            t = Table(table_data, colWidths=[half_w, half_w])
            t.setStyle(TableStyle([
                ("SPAN", (0, 0), (1, 0)),
                ("BACKGROUND", (0, 0), (-1, -1), c_bg_light),
                ("BOX", (0, 0), (-1, -1), 0.5, c_border),
                ("LINEBELOW", (0, 0), (1, 0), 0.5, c_border),
                ("LINEBEFORE", (0, 1), (0, 1), 2.5, c_primary),
                ("LINEBEFORE", (1, 1), (1, 1), 2.5, c_rose),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            dis_elements.append(t)
            dis_elements.append(Spacer(1, 6))

        story.append(KeepTogether(dis_elements[:2]))
        for elem in dis_elements[2:]:
            story.append(elem)
        story.append(Spacer(1, 10))

    # -------------------------------------------------------------
    # 7. TARGET AUDIENCE MATRIX (BUYING GUIDE)
    # -------------------------------------------------------------
    who_buy = payload.get("who_should_buy", [])
    who_avoid = payload.get("who_should_avoid", [])
    if who_buy or who_avoid:
        story.append(Paragraph("4. CONSUMER DECISION MATRIX (WHO SHOULD BUY VS. AVOID)", style_sec_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=c_border, spaceBefore=2, spaceAfter=8))

        buy_items: list[Any] = [Paragraph("<b>IDEAL CANDIDATES (CONSIDER BUYING)</b>", ParagraphStyle("BuyH", parent=style_kicker, textColor=c_emerald))]
        for item in who_buy:
            buy_items.append(Paragraph(f"<b>[+]</b> {_sanitize(item)}", ParagraphStyle("BuyItem", parent=style_body, fontSize=8.5, leading=12, textColor=colors.HexColor("#065F46"))))

        avoid_items: list[Any] = [Paragraph("<b>UNSUITABLE CANDIDATES (LOOK CLOSER / AVOID)</b>", ParagraphStyle("AvoidH", parent=style_kicker, textColor=c_rose))]
        for item in who_avoid:
            avoid_items.append(Paragraph(f"<b>[-]</b> {_sanitize(item)}", ParagraphStyle("AvoidItem", parent=style_body, fontSize=8.5, leading=12, textColor=colors.HexColor("#9F1239"))))

        matrix_table = Table([[buy_items, avoid_items]], colWidths=[half_w, half_w])
        matrix_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#ECFDF5")),
            ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#FFF1F2")),
            ("BOX", (0, 0), (0, 0), 1, colors.HexColor("#A7F3D0")),
            ("BOX", (1, 0), (1, 0), 1, colors.HexColor("#FECDD3")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(KeepTogether([matrix_table]))
        story.append(Spacer(1, 14))

    # -------------------------------------------------------------
    # 8. SOURCE INTELLIGENCE DOSSIER (ORIGINAL REVIEWS)
    # -------------------------------------------------------------
    if sources_list:
        story.append(Paragraph("5. PRIMARY SOURCE DOSSIER &amp; TRANSCRIPT CITATIONS", style_sec_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=c_border, spaceBefore=2, spaceAfter=8))
        story.append(Paragraph("<i>All claims synthesized in this report were verified against timestamped transcripts from these technical reviews:</i>", style_meta))
        story.append(Spacer(1, 4))

        src_elements: list[Any] = []
        for idx, src in enumerate(sources_list, 1):
            title = src.get("title", "Review Video")
            channel = src.get("channel", "Reviewer")
            views = src.get("views")
            views_str = f"{views:,} views" if views else "Views unlisted"
            dur_sec = src.get("duration_seconds")
            dur_str = f"{dur_sec // 60}:{dur_sec % 60:02d}" if dur_sec else "N/A"
            period = src.get("usage_period") or "Stated testing period"
            rec = src.get("recommendation_summary", "")

            src_table_data = [
                [
                    Paragraph(f"<b>SOURCE {idx:02d}: {_sanitize(title)}</b>", style_body_bold),
                    Paragraph(f"<b>{_sanitize(channel)}</b>", ParagraphStyle("Chan", parent=style_body, alignment=2, fontName="Helvetica-Bold", textColor=c_primary)),
                ],
                [
                    Paragraph(f"Duration: {dur_str} · {views_str} · Usage Period: <b>{period}</b>", style_meta),
                    "",
                ],
                [
                    Paragraph(f"<b>Reviewer Takeaway:</b> {_sanitize(rec)}", style_body),
                    "",
                ]
            ]
            t = Table(src_table_data, colWidths=[8.5 * inch - 108 - 140, 140])
            t.setStyle(TableStyle([
                ("SPAN", (0, 1), (1, 1)),
                ("SPAN", (0, 2), (1, 2)),
                ("BACKGROUND", (0, 0), (-1, -1), c_bg_light),
                ("BOX", (0, 0), (-1, -1), 0.5, c_border),
                ("LINEBEFORE", (0, 0), (0, -1), 2.5, c_primary),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]))
            src_elements.append(t)
            if "sample_used" in src:
                sample = SampleUsed.model_validate(src["sample_used"] or {})
                if not sample.units:
                    src_elements.append(Paragraph("<b>Sample used:</b> Unconfirmed", style_meta))
                else:
                    for unit in sample.units:
                        details = []
                        for detail in unit.details:
                            links = _evidence_links((detail.evidence,))
                            details.append(f"{_sanitize(detail.label)}: {_sanitize(detail.value)}" + (f" ({links})" if links else ""))
                        src_elements.append(Paragraph(
                            f"<b>Sample used - {_sanitize(unit.role)}:</b> " + "; ".join(details), style_meta,
                        ))
                    src_elements.append(Paragraph("Other sample details: Unconfirmed", style_meta))
            src_elements.append(Spacer(1, 6))

        for elem in src_elements:
            story.append(elem)

    # -------------------------------------------------------------
    # 9. DOCUMENT FOOTER SIGNATURE
    # -------------------------------------------------------------
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=c_border, spaceBefore=4, spaceAfter=6))
    story.append(Paragraph(
        f"<b>ReviewLens AI Grounded Verification Engine</b> &bull; Cryptographic Token: <font face='Courier'>{token}</font> &bull; "
        "Every claim traceable to timestamped original source transcript.",
        ParagraphStyle("DocFooter", parent=style_meta, alignment=1)
    ))

    # Pass product name to canvas for running header
    def on_first_page(canvas_obj: canvas.Canvas, _doc: Any) -> None:
        setattr(canvas_obj, "product_title", product_name)

    def on_later_pages(canvas_obj: canvas.Canvas, _doc: Any) -> None:
        setattr(canvas_obj, "product_title", product_name)

    doc.build(story, canvasmaker=NumberedCanvas, onFirstPage=on_first_page, onLaterPages=on_later_pages)
    return buffer.getvalue()
