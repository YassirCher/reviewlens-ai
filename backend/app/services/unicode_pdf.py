"""Offline Unicode print rendering with packaged fonts and bounded resources."""
from __future__ import annotations

import base64
import html
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any

_SLOTS = BoundedSemaphore(2)
_FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"
_VIDEO = re.compile(r"^[A-Za-z0-9_-]{11}$")


def needs_unicode_renderer(payload: dict[str, Any]) -> bool:
    return bool(re.search(r"[\u0370-\u052f\u0590-\u08ff\u0900-\u0dff\u2e80-\ua4cf\uac00-\ud7af]", json.dumps(payload, ensure_ascii=False)))


def _text(value: Any) -> str:
    return html.escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value if value is not None else "")))


def report_html(payload: dict[str, Any], token: str, *, citation_texts: list[str] | None = None) -> str:
    """Render only public report fields. User strings are always text, never HTML."""
    serialized = json.dumps(payload, ensure_ascii=False)
    faces = []
    families = [("Noto", "NotoSans-Regular.ttf"), ("Devanagari", "NotoSansDevanagari-Regular.ttf"),
                ("Arabic", "NotoSansArabic-Regular.ttf")]
    if re.search(r"[\u2e80-\ua4cf]", serialized):
        families.append(("CJK", "NotoSansSC-VF.ttf"))
    for family, name in families:
        data = base64.b64encode((_FONTS / name).read_bytes()).decode("ascii")
        faces.append(f"@font-face{{font-family:{family};src:url(data:font/ttf;base64,{data})}}")
    parts = ["<!doctype html><html><head><meta charset='utf-8'><style>", *faces,
        "@page{size:Letter;margin:18mm 17mm 20mm}body{font-family:Noto,Devanagari,Arabic,CJK,sans-serif;font-size:10pt;line-height:1.6;color:#0f172a}h1{font-size:24pt}h2{font-size:15pt;color:#0369a1;margin-top:24pt;break-after:avoid}h3{break-after:avoid}p,li,blockquote{overflow-wrap:anywhere;unicode-bidi:plaintext}.citation{break-inside:avoid}blockquote{border-left:3px solid #cbd5e1;margin:8pt 0;padding:5pt 12pt;background:#f8fafc}a{color:#0369a1;font-size:9pt}.meta{color:#475569;font-size:9pt}.source{border-top:1px solid #cbd5e1;margin-top:16pt}li{margin:4pt 0}",
        "</style></head><body>", f"<h1>{_text(payload.get('product_name', 'Product analysis'))}</h1>"]

    def paragraph(value: Any, tag: str = "p") -> None:
        if value:
            parts.append(f"<{tag} dir='auto'>{_text(value)}</{tag}>")

    def listing(values: list) -> None:
        if values:
            parts.append("<ul>")
            for value in values:
                paragraph(value, "li")
            parts.append("</ul>")

    def citation(evidence: dict, video_id: str = "") -> None:
        parts.append("<div class='citation'>")
        quote = evidence.get("text") or evidence.get("excerpt")
        paragraph(quote, "blockquote")
        if quote and citation_texts is not None:
            citation_texts.append(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(quote)))
        video_id = evidence.get("video_id") or video_id
        if not isinstance(video_id, str) or not _VIDEO.fullmatch(video_id):
            parts.append("</div>")
            return
        timestamp = evidence.get("timestamp_start_seconds", evidence.get("timestamp_seconds"))
        seconds = int(timestamp) if isinstance(timestamp, (float, int)) and 0 <= timestamp < 864000 else None
        url = f"https://www.youtube.com/watch?v={video_id}" + (f"&t={seconds}s" if seconds is not None else "")
        label = f"{seconds // 60}:{seconds % 60:02d} in original review" if seconds is not None else "Open original review"
        parts.append(f"<p><a href='{html.escape(url, quote=True)}'>{label}</a></p>")
        parts.append("</div>")

    paragraph(f"{payload.get('source_count_analyzed', 0)} / {payload.get('source_count_requested', 0)} source reviews; score {payload.get('overall_score', 'Unconfirmed')}; confidence {payload.get('confidence', 'Unconfirmed')}.")
    paragraph(payload.get("verdict"))
    paragraph("Research summary", "h2")
    paragraph(payload.get("summary"))
    source_names = {str(item.get('id')): str(item.get('channel') or 'Reviewer') for item in payload.get('sources', [])}
    for title, field in (("Strengths", "consensus_pros"), ("Caveats", "consensus_cons")):
        paragraph(title, "h2")
        for finding in payload.get(field, []):
            paragraph(finding.get("statement"))
            paragraph("Cited reviewers: " + ', '.join(source_names.get(str(ref), 'Reviewer') for ref in finding.get('source_ids', [])))
    if payload.get("disagreements"):
        paragraph("Reviewer disagreements", "h2")
        for item in payload["disagreements"]:
            for key in ("topic", "side_a", "side_b"):
                paragraph(item.get(key))
    guide = payload.get("decision_guide") or {}
    paragraph("Purchase check", "h2")
    findings = {item.get("id"): item for field in ("consensus_pros", "consensus_cons") for item in payload.get(field, [])}
    for title, key in (("Buy if these strengths matter", "buy_if_finding_ids"), ("Think twice about", "caveat_finding_ids")):
        paragraph(title, "h3")
        listing([findings[ref]["statement"] for ref in guide.get(key, []) if ref in findings])
    paragraph(f"Longest stated use: {guide.get('long_term_period') or payload.get('longest_usage_period') or 'Not established'}")
    listing(guide.get("unknowns", []))
    if not guide:
        listing(payload.get("who_should_buy", []))
        listing(payload.get("who_should_avoid", []))
    info = payload.get("product_info") or {}
    if info:
        paragraph("Product details from reviews", "h2")
        paragraph(info.get("coverage_note"))
        for field in ("facts", "variants"):
            for item in info.get(field, []):
                paragraph(f"{item.get('label', item.get('dimension', ''))}: {item.get('value', '')}" + (f" ({item['scope']})" if item.get('scope') else ""))
                if item.get('conflicting'):
                    paragraph('Conflicting review statements')
                for evidence in item.get("evidence", []):
                    citation(evidence)
    audience = payload.get("comment_analysis") or {}
    if audience:
        paragraph("Audience comments", "h2")
        paragraph(audience.get("status"))
        paragraph('; '.join(f"{audience.get(key, 0)} {label}" for key, label in (("comments_sampled", "sampled"), ("comments_retained", "retained"), ("comments_relevant", "relevant"), ("comments_translated", "translated"))))
        listing(audience.get("limitations", []))
    paragraph("Limitations", "h2")
    listing(payload.get("limitations", []))
    listing([str(value).replace('_', ' ') for value in payload.get("warnings", [])])
    paragraph("Primary source dossier and original citations", "h2")
    for index, source in enumerate(payload.get("sources", []), 1):
        parts.append("<section class='source'>")
        paragraph(f"Source {index}: {source.get('title', 'Review video')}", "h3")
        paragraph(source.get("channel"))
        paragraph(f"Usage period: {source.get('usage_period') or 'Not established'}")
        paragraph(source.get("recommendation_summary"))
        for field in ("pros", "cons", "limitations"):
            listing(source.get(field, []))
        for claim in source.get("claims", []):
            paragraph(claim.get("claim"))
            for evidence in claim.get("evidence", []):
                citation(evidence, source.get("video_id", ""))
        sample = source.get("sample_used") or {}
        if not sample.get("units"):
            paragraph("Sample used: Unconfirmed")
        for unit in sample.get("units", []):
            paragraph(f"Sample used: {unit.get('role', 'Review unit')}")
            for detail in unit.get("details", []):
                paragraph(f"{detail.get('label', '')}: {detail.get('value', '')}")
                citation(detail.get("evidence", {}), source.get("video_id", ""))
        parts.append("</section>")
    paragraph(f"ReviewLens research report · Reference {token}")
    parts.append("</body></html>")
    return ''.join(parts)


def generate_unicode_pdf(payload: dict[str, Any], token: str) -> bytes:
    if not _SLOTS.acquire(timeout=10):
        raise RuntimeError("PDF renderer busy; retry the export")
    try:
        # A subprocess bounds the entire export, including printToPDF. Closing
        # its pipes also closes the Playwright driver/browser when interrupted.
        result = subprocess.run([sys.executable, "-m", "app.services.unicode_pdf"],
            input=json.dumps({"payload": payload, "token": token}, ensure_ascii=False).encode("utf-8"),
            cwd=Path(__file__).resolve().parents[2], capture_output=True, timeout=45, check=True)
        if not result.stdout.startswith(b"%PDF-"):
            raise RuntimeError("PDF renderer returned an invalid document")
        return result.stdout
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError("PDF export unavailable; retry the export") from exc
    finally:
        _SLOTS.release()


def _quote_actual_text(data: bytes, quotes: list[str]) -> bytes:
    """Attach exact logical text to rendered quotation spans for accessible extraction."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ContentStream, DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    page_ids = {page.indirect_reference.idnum: index for index, page in enumerate(writer.pages)}
    text_owners: dict[tuple[int, int], str] = {}
    selected = 0

    def quote_text_nodes(value: Any) -> list[tuple[int, int]]:
        value = value.get_object() if hasattr(value, 'get_object') else value
        if isinstance(value, list):
            return [item for child in value for item in quote_text_nodes(child)]
        if not isinstance(value, dict):
            return []
        if value.get('/S') == '/NonStruct' and isinstance(value.get('/K'), int) and value.get('/Pg'):
            return [(page_ids[value['/Pg'].indirect_reference.idnum], int(value['/K']))]
        return quote_text_nodes(value.get('/K', []))

    def visit(value: Any) -> None:
        nonlocal selected
        value = value.get_object() if hasattr(value, 'get_object') else value
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            if value.get('/S') == '/BlockQuote':
                # Chromium tags the decoration separately from the glyphs.
                # Bind the replacement to the actual text child, not its box.
                owners = quote_text_nodes(value.get('/K', []))
                if len(owners) != 1 or selected >= len(quotes) or owners[0] in text_owners:
                    raise RuntimeError('PDF quotation structure mismatch')
                value[NameObject('/ActualText')] = TextStringObject(quotes[selected])
                text_owners[owners[0]] = quotes[selected]
                selected += 1
            elif '/K' in value:
                visit(value['/K'])

    visit(writer.root_object.get('/StructTreeRoot', {}))
    if selected != len(quotes):
        raise RuntimeError('PDF quotation count mismatch')
    bound = 0
    for page_index, page in enumerate(writer.pages):
        stream = ContentStream(page.get_contents(), writer)
        operations = []
        depth, quote_depth = 0, None
        for operands, operation in stream.operations:
            if operation in (b"BDC", b"BMC"):
                depth += 1
                mcid = operands[1].get('/MCID') if operation == b'BDC' and isinstance(operands[1], dict) else None
                key = (page_index, mcid)
                if key in text_owners:
                    if quote_depth is not None:
                        raise RuntimeError('PDF quotation structure mismatch')
                    operations.append((operands, operation))
                    operations.append(([NameObject('/Span'), DictionaryObject({
                        NameObject('/ActualText'): TextStringObject(text_owners[key])})], b'BDC'))
                    bound += 1
                    quote_depth = depth
                    continue
                if quote_depth is not None and operation == b'BDC' and isinstance(operands[1], DictionaryObject):
                    # One outer logical replacement covers the entire quotation.
                    operands[1].pop('/ActualText', None)
            elif operation == b'EMC':
                if quote_depth == depth:
                    operations.append(([], b'EMC'))
                    quote_depth = None
                depth -= 1
            operations.append((operands, operation))
        if quote_depth is not None:
            raise RuntimeError('PDF quotation structure incomplete')
        stream.operations = operations
        page.replace_contents(stream)
    if bound != len(quotes):
        raise RuntimeError('PDF quotation count mismatch')
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _render(payload: dict[str, Any], token: str) -> bytes:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, timeout=15000)
        try:
            context = browser.new_context(java_script_enabled=False, service_workers="block")
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(15000)
            quotes: list[str] = []
            page.set_content(report_html(payload, token, citation_texts=quotes), wait_until="load", timeout=15000)
            # Loading the document is earlier than loading fallback fonts. Print
            # only after layout has its fonts; otherwise long Hindi reports can
            # contain blank quotations despite intact HTML text.
            page.evaluate("document.fonts.ready")
            data = page.pdf(format="Letter", print_background=True, display_header_footer=True,
                header_template="<span></span>", footer_template="<div style='width:100%;text-align:center;font-size:8px'>ReviewLens · <span class='pageNumber'></span> / <span class='totalPages'></span></div>",
                prefer_css_page_size=True, tagged=True)
            return _quote_actual_text(data, quotes)
        finally:
            browser.close()


if __name__ == "__main__":
    request = json.loads(sys.stdin.buffer.read())
    sys.stdout.buffer.write(_render(request["payload"], request["token"]))
