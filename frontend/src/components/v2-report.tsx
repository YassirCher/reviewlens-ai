/* eslint-disable @next/next/no-img-element -- public YouTube thumbnails have a local fallback. */
"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, Check, Clock3, Download, ExternalLink, GitBranch, Info, Play, Share2, ShieldCheck, Youtube } from "lucide-react";
import type { Evidence, Finding, Report, Source } from "@/lib/v2";
import { V2Graph } from "./v2-graph";
import { ProductInformationCard, SampleUsedBlock } from "./v2-product-info";

const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

function verdictLabel(value: string): string { return value.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase()); }
function duration(value: number | null): string { if (value == null) return "Duration unknown"; return `${Math.floor(value / 60)}:${String(value % 60).padStart(2,"0")}`; }
function videoLink(source: Source, evidence?: Evidence): string | null {
  if (!VIDEO_ID.test(source.video_id)) return null;
  const seconds = evidence?.timestamp_start_seconds;
  return `https://www.youtube.com/watch?v=${source.video_id}${seconds == null ? "" : `&t=${Math.max(0, Math.floor(seconds))}s`}`;
}
function time(value: number | null): string { if (value == null) return "Open source"; return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2,"0")}`; }
function reportDate(value: string): string { return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", year: "numeric", month: "short", day: "numeric" }).format(new Date(value)); }

function FindingCard({ finding, report, tone }: { finding: Finding; report: Report; tone: "positive" | "caution" }) {
  const relevant = report.sources.flatMap(source => source.claims.flatMap(claim => claim.evidence.filter(item => finding.evidence_ids.includes(item.id)).map(item => ({ source, item }))));
  return <details className={`v2-finding v2-finding-${tone}`}><summary><span className="v2-finding-mark" aria-hidden="true" /> <span>{finding.statement}<small>{finding.source_ids.length} independent source{finding.source_ids.length === 1 ? "" : "s"} · Inspect evidence</small></span><ArrowRight size={17} aria-hidden="true" /></summary><div className="v2-finding-body">{relevant.length ? relevant.map(({source,item}) => <div key={`${source.id}-${item.id}`} className="v2-excerpt"><p>“{item.text}”</p><div><span>{source.channel}</span>{videoLink(source,item) && <a href={videoLink(source,item)!} target="_blank" rel="noopener noreferrer">{time(item.timestamp_start_seconds)} <ExternalLink size={13} aria-hidden="true" /><span className="v2-sr-only"> in YouTube, new tab</span></a>}</div></div>) : <p className="v2-muted">Evidence unavailable in this projection.</p>}</div></details>;
}

function SourceCard({ source, index }: { source: Source; index: number }) {
  const url = videoLink(source);
  return (
    <article className="v2-source-card" id={`source-${source.id}`}>
      <div className="v2-source-header">
        <a
          className="v2-source-art"
          href={url || `https://www.youtube.com/watch?v=${source.video_id}`}
          target="_blank"
          rel="noopener noreferrer"
          title={`Watch "${source.title}" on YouTube (opens new tab)`}
        >
          <Youtube size={32} className="v2-source-yt-placeholder" aria-hidden="true" />
          {VIDEO_ID.test(source.video_id) && (
            <img
              src={`https://i.ytimg.com/vi/${source.video_id}/hqdefault.jpg`}
              alt={`Thumbnail for ${source.title}`}
              loading="lazy"
              onError={event => { event.currentTarget.hidden = true; }}
            />
          )}
          <div className="v2-source-play-overlay" aria-hidden="true">
            <span className="v2-source-play-btn">
              <Play size={18} fill="currentColor" />
            </span>
          </div>
          {source.duration_seconds != null && (
            <span className="v2-source-duration">{duration(source.duration_seconds)}</span>
          )}
          <span className="v2-source-yt-pill" aria-hidden="true">
            <Youtube size={12} fill="#FF0000" strokeWidth={0} />
            YouTube
          </span>
        </a>
        <div className="v2-source-header-info">
          <div className="v2-source-top">
            <span className="v2-eyebrow">SOURCE {String(index + 1).padStart(2, "0")}</span>
            <span className="v2-score-mini">{source.source_score}<small>/100 source score</small></span>
          </div>
          <h3>{source.title}</h3>
          <p className="v2-muted v2-source-byline">
            <span className="v2-channel-name">{source.channel}</span> · {source.views == null ? "Views unavailable" : `${new Intl.NumberFormat("en", { notation: "compact" }).format(source.views)} views`} · {duration(source.duration_seconds)}
          </p>
          {url && (
            <a className="v2-source-link" href={url} target="_blank" rel="noopener noreferrer">
              Watch original review <ArrowUpRight size={15} aria-hidden="true" /><span className="v2-sr-only"> in new tab</span>
            </a>
          )}
        </div>
      </div>
      <div className="v2-source-body">
        <p className="v2-source-summary">{source.recommendation_summary}</p>
        <div className="v2-tags">
          <span>{verdictLabel(source.review_type)}</span>
          <span>{verdictLabel(source.ownership_context)}</span>
          <span>Evidence quality {source.evidence_quality_score}/100</span>
        </div>
        <p className="v2-source-context">
          <Clock3 size={15} aria-hidden="true" /> Usage period: {source.usage_period || "Not established"} · Captions: {source.caption_kind}, {source.transcript_language}{source.translated ? " (translated)" : ""}
        </p>
        {source.claims.length > 0 && (
          <details className="v2-source-claims">
            <summary>Inspect {source.claims.length} claim{source.claims.length === 1 ? "" : "s"} and evidence</summary>
            <div>{source.claims.map((claim, claimIndex) => (
              <div key={`${claim.claim}-${claimIndex}`} className="v2-claim">
                <strong>{claim.claim}</strong>
                {claim.evidence.map(item => (
                  <div key={item.id} className="v2-excerpt" id={`evidence-${item.id}`}>
                    <p>“{item.text}”</p>
                    <div>
                      <span>{item.support_type === "contradicts" ? "Contradicts" : "Supports"} · Evidence confidence {item.confidence}/100</span>
                      {url && <a href={videoLink(source,item)!} target="_blank" rel="noopener noreferrer">{time(item.timestamp_start_seconds)} <ExternalLink size={13} aria-hidden="true" /><span className="v2-sr-only"> in YouTube, new tab</span></a>}
                    </div>
                  </div>
                ))}
              </div>
            ))}</div>
          </details>
        )}
        <SampleUsedBlock sample={source.sample_used} />
        {source.limitations.length > 0 && <p className="v2-source-context"><Info size={15} aria-hidden="true" /> {source.limitations.join(" · ")}</p>}
      </div>
    </article>
  );
}

export function V2Report({ report, token }: { report: Report; token: string }) {
  const [copied, setCopied] = useState(false);
  const sourceById = new Map(report.sources.map(source => [source.id, source]));

  async function share() {
    try { await navigator.clipboard.writeText(window.location.href); setCopied(true); setTimeout(() => setCopied(false), 3000); }
    catch { setCopied(false); }
  }

  return <>
    <div className="v2-print-header" aria-hidden="true">
      <div className="v2-print-brand-row">
        <div className="v2-print-brand">
          <span className="v2-print-badge">REVIEWLENS RESEARCH DOSSIER</span>
          <h2 className="v2-print-title">{report.product_name}</h2>
        </div>
        <div className="v2-print-score-pill">
          <div className="v2-print-score-val">{report.overall_score}<small>/100</small></div>
          <div className="v2-print-score-tag">{verdictLabel(report.verdict)}</div>
        </div>
      </div>
      <div className="v2-print-meta-grid">
        <div><span>Date:</span> {reportDate(report.generated_at)} UTC</div>
        <div><span>Confidence:</span> {report.confidence}% ({verdictLabel(report.confidence_band)})</div>
        <div><span>Sources:</span> {report.source_count_analyzed} of {report.source_count_requested} analyzed</div>
        <div><span>Report Token:</span> <code>{token}</code></div>
      </div>
    </div>

    <div className="v2-report-top">
      <div>
        <p className="v2-eyebrow">RESEARCH REPORT · {reportDate(report.generated_at)}</p>
        <h1>{report.product_name}</h1>
        <p>{report.source_count_analyzed} of {report.source_count_requested} requested sources analyzed {report.status === "partial" && <span className="v2-partial">PARTIAL COVERAGE</span>}</p>
      </div>
      <div className="v2-report-actions">
        <a
          className="v2-button-secondary"
          href={`${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/api/v2/reports/${token}/pdf`}
          download={`ReviewLens-${report.product_name.replace(/[^a-zA-Z0-9_-]+/g, "-")}-Dossier.pdf`}
          title="Download comprehensive PDF intelligence dossier"
        >
          <Download size={16} aria-hidden="true" />
          Download PDF
        </a>
        <button className="v2-button-secondary" onClick={share}>
          <Share2 size={16} aria-hidden="true" />
          {copied ? "Link copied" : "Copy report link"}
        </button>
      </div>
    </div>
    <ProductInformationCard info={report.product_info} />
    <section className="v2-report-hero" aria-labelledby="v2-verdict"><div className="v2-verdict-block"><span className="v2-eyebrow">BUYING SIGNAL</span><div className="v2-score">{report.overall_score}<span>/100</span></div><h2 id="v2-verdict">{verdictLabel(report.verdict)}</h2><p>Overall product score</p></div><div className="v2-report-intro"><span className="v2-eyebrow">THE BOTTOM LINE</span><p>{report.summary}</p><div className="v2-confidence"><ShieldCheck size={19} aria-hidden="true" /><div><strong>{report.confidence}% confidence · {verdictLabel(report.confidence_band)}</strong><span>Confidence reflects evidence quality and coverage—not product quality.</span></div></div></div></section>
    <section className="v2-footprint" aria-label="Analysis footprint"><div><strong>{report.source_count_analyzed}<span> / {report.source_count_requested}</span></strong><small>source reviews</small></div><div><strong>{report.total_tokens.toLocaleString()}</strong><small>tokens used{report.usage_pending ? " · accounting pending" : ""}</small></div><div><strong>{report.model_call_count}</strong><small>model calls</small></div></section>
    {report.status === "partial" && <div className="v2-alert v2-alert-warning" role="status"><strong>Partial evidence.</strong> Some requested sources were unavailable or could not be analyzed. Read the limitations before relying on the conclusion.</div>}
    {(report.warnings.length > 0 || report.limitations.length > 0) && <section className="v2-report-notes"><h2>Important context</h2><ul>{[...report.warnings,...report.limitations].map((item,index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></section>}

    <section className="v2-report-section" aria-labelledby="v2-findings"><div className="v2-section-top"><div><p className="v2-eyebrow">CROSS-SOURCE READ</p><h2 id="v2-findings">Where reviewers converge</h2></div><span className="v2-step">02 — FINDINGS</span></div><div className="v2-findings-grid"><div><h3 className="v2-positive">Strengths</h3>{report.consensus_pros.length ? report.consensus_pros.map(finding => <FindingCard key={finding.id} finding={finding} report={report} tone="positive" />) : <p className="v2-muted">No independently repeated strength was established.</p>}</div><div><h3 className="v2-caution">Caveats</h3>{report.consensus_cons.length ? report.consensus_cons.map(finding => <FindingCard key={finding.id} finding={finding} report={report} tone="caution" />) : <p className="v2-muted">No independently repeated caveat was established.</p>}</div></div></section>
    {report.disagreements.length > 0 && <section className="v2-report-section v2-disagreements"><p className="v2-eyebrow">WHERE REVIEWERS DIFFER</p><h2>Disagreement matters.</h2>{report.disagreements.map((item,index) => <div key={`${item.topic}-${index}`} className="v2-disagreement"><h3>{item.topic}</h3><div><p><span>ONE VIEW</span>{item.side_a}<small>{item.side_a_source_ids.map(id => sourceById.get(id)?.channel).filter(Boolean).join(", ")}</small></p><p><span>ANOTHER VIEW</span>{item.side_b}<small>{item.side_b_source_ids.map(id => sourceById.get(id)?.channel).filter(Boolean).join(", ")}</small></p></div></div>)}</section>}
    <section className="v2-report-section v2-fit-grid"><div className="v2-panel"><p className="v2-eyebrow">BEST FOR</p><h2>Consider buying if…</h2><ul>{report.who_should_buy.length ? report.who_should_buy.map((item,index) => <li key={`${item}-${index}`}><Check size={16} aria-hidden="true" />{item}</li>) : <li>No specific buyer fit was established.</li>}</ul></div><div className="v2-panel"><p className="v2-eyebrow">THINK TWICE</p><h2>Look closer if…</h2><ul>{report.who_should_avoid.length ? report.who_should_avoid.map((item,index) => <li key={`${item}-${index}`}><Info size={16} aria-hidden="true" />{item}</li>) : <li>No specific avoidance guidance was established.</li>}</ul></div></section>
    <section className="v2-usage v2-panel"><Clock3 size={22} aria-hidden="true" /><div><p className="v2-eyebrow">LONG-TERM EVIDENCE</p><h2>{report.longest_usage_period || "No clear usage period established"}</h2><p>{report.longest_usage_source_id && sourceById.get(report.longest_usage_source_id) ? `Longest stated period from ${sourceById.get(report.longest_usage_source_id)!.channel}.` : "Do not assume these reviews establish long-term reliability."}</p></div></section>
    <section className="v2-report-section" aria-labelledby="v2-sources"><div className="v2-section-top"><div><p className="v2-eyebrow">ORIGINAL REVIEWS</p><h2 id="v2-sources">Inspect every source</h2><p>Claims are paired with short excerpts and links to the matching video moment.</p></div><span className="v2-step">03 — SOURCES</span></div><div className="v2-source-list">{report.sources.map((source,index) => <SourceCard key={source.id} source={source} index={index} />)}</div></section>
    <section className="v2-report-section" aria-labelledby="v2-map"><div className="v2-section-top"><div><p className="v2-eyebrow">EVIDENCE STRUCTURE</p><h2 id="v2-map">Follow the connections</h2><p>A public-safe view of how sources, findings, and excerpts relate.</p></div><Link className="v2-text-link" href={`/r/${token}/evidence`}>Open full evidence map <ArrowRight size={17} aria-hidden="true" /></Link></div><V2Graph report={report} token={token} /></section>
    <div className="v2-report-bottom"><span><GitBranch size={17} aria-hidden="true" /> Every central claim must be traceable before a report is published.</span><Link href="/">Research another product <ArrowRight size={16} aria-hidden="true" /></Link></div>

    <div className="v2-print-footer" aria-hidden="true">
      <div className="v2-print-footer-inner">
        <div>ReviewLens AI · Verified Grounded Product Intelligence</div>
        <div>Every claim traceable to timestamped source evidence · {token}</div>
      </div>
    </div>
  </>;
}
