"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink, MessageSquareText, Timer, ThumbsDown, ThumbsUp } from "lucide-react";
import type { VideoAnalysis } from "@/lib/types";
import { Badge, Divider, SectionLabel } from "./ui";
import { ScoreRing } from "./score-ring";

function views(n: number) {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

function timestamp(seconds: number | null) {
  if (seconds == null) return null;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function verdictTone(v: VideoAnalysis["purchase_verdict"]): "good" | "warn" | "bad" | "neutral" {
  if (v === "buy") return "good";
  if (v === "buy_with_caveats" || v === "mixed") return "warn";
  if (v === "do_not_buy") return "bad";
  return "neutral";
}

export function VideoCard({ analysis, index }: { analysis: VideoAnalysis; index: number }) {
  const [open, setOpen] = useState(index === 0);
  const c = analysis.comments;
  return (
    <article className="glass overflow-hidden rounded-[26px]">
      <button className="w-full p-4 text-left sm:p-5" onClick={() => setOpen((v) => !v)}>
        <div className="flex gap-4">
          <div className="relative hidden h-[92px] w-[154px] shrink-0 overflow-hidden rounded-2xl bg-slate-900 sm:block">
            {analysis.video.thumbnail_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={analysis.video.thumbnail_url} alt="" className="h-full w-full object-cover" />
            ) : null}
            <div className="absolute left-2 top-2 rounded-lg border border-white/15 bg-black/60 px-2 py-1 text-[10px] font-bold text-white backdrop-blur">#{index + 1}</div>
          </div>
          <div className="min-w-0 flex-1 py-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge tone={verdictTone(analysis.purchase_verdict)}>{analysis.purchase_verdict.replaceAll("_", " ").toUpperCase()}</Badge>
              <Badge>{analysis.review_type.replaceAll("_", " ")}</Badge>
            </div>
            <h3 className="line-clamp-2 text-[15px] font-semibold leading-6 text-white sm:text-base">{analysis.video.title}</h3>
            <p className="mt-1.5 text-xs text-slate-500">{analysis.video.channel} · {views(analysis.video.view_count)} views</p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <ScoreRing score={analysis.product_score} size="sm" />
            <ChevronDown className={`h-4 w-4 text-slate-500 transition ${open ? "rotate-180" : ""}`} />
          </div>
        </div>
      </button>

      {open && (
        <div className="px-5 pb-5 sm:px-6 sm:pb-6">
          <Divider />
          <div className="grid gap-6 pt-6 lg:grid-cols-[1.15fr_.85fr]">
            <div>
              <SectionLabel>Reviewer conclusion</SectionLabel>
              <p className="text-sm leading-6 text-slate-300">{analysis.recommendation_summary}</p>

              <div className="mt-5 grid grid-cols-3 gap-2">
                {[
                  ["Sentiment", analysis.reviewer_sentiment_score],
                  ["Buy signal", analysis.purchase_recommendation_score],
                  ["Confidence", analysis.confidence_score],
                ].map(([label, value]) => (
                  <div key={String(label)} className="soft-card rounded-2xl p-3">
                    <div className="text-lg font-semibold text-white">{value}</div>
                    <div className="mt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>
                  </div>
                ))}
              </div>

              <div className="mt-6 grid gap-5 sm:grid-cols-2">
                <div>
                  <SectionLabel>What they liked</SectionLabel>
                  <ul className="space-y-2.5">
                    {analysis.pros.slice(0, 5).map((item) => <li key={item} className="flex gap-2.5 text-sm leading-5 text-slate-300"><ThumbsUp className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" />{item}</li>)}
                  </ul>
                </div>
                <div>
                  <SectionLabel>What they disliked</SectionLabel>
                  <ul className="space-y-2.5">
                    {analysis.cons.slice(0, 5).map((item) => <li key={item} className="flex gap-2.5 text-sm leading-5 text-slate-300"><ThumbsDown className="mt-0.5 h-4 w-4 shrink-0 text-rose-300" />{item}</li>)}
                  </ul>
                </div>
              </div>
            </div>

            <div className="space-y-4">
              <div className="soft-card rounded-2xl p-4">
                <SectionLabel>Usage evidence</SectionLabel>
                <div className="flex items-start gap-3">
                  <Timer className="mt-0.5 h-4 w-4 text-indigo-300" />
                  <div>
                    <p className="text-sm font-medium text-white">{analysis.usage_period_raw || "Not stated"}</p>
                    <p className="mt-1 text-xs text-slate-500">Ownership: {analysis.ownership_context.replaceAll("_", " ")}</p>
                  </div>
                </div>
              </div>

              {analysis.evidence.length > 0 && (
                <div className="soft-card rounded-2xl p-4">
                  <SectionLabel>Evidence trail</SectionLabel>
                  <div className="space-y-3">
                    {analysis.evidence.slice(0, 4).map((ev, i) => (
                      <div key={`${ev.claim}-${i}`} className="border-l border-indigo-300/30 pl-3">
                        <div className="flex items-center gap-2">
                          <p className="text-xs font-semibold text-slate-200">{ev.claim}</p>
                          {timestamp(ev.timestamp_seconds) && <span className="text-[10px] text-indigo-300">{timestamp(ev.timestamp_seconds)}</span>}
                        </div>
                        <p className="mt-1 text-xs leading-5 text-slate-500">{ev.evidence_text}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <a href={analysis.video.url} target="_blank" rel="noreferrer" className="flex items-center justify-center gap-2 rounded-2xl border border-white/10 bg-white/[.035] px-4 py-3 text-xs font-semibold text-slate-300 transition hover:border-white/20 hover:bg-white/[.06] hover:text-white">
                Open source video <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>
          </div>

          {c && (
            <div className="mt-6 rounded-2xl border border-white/[.07] bg-black/10 p-4 sm:p-5">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2 text-sm font-semibold text-white"><MessageSquareText className="h-4 w-4 text-indigo-300" /> Audience pulse</div>
                  <p className="mt-1 text-xs text-slate-500">{c.comments_analyzed} relevant top comments analyzed</p>
                </div>
                <Badge tone={c.audience_agrees_with_reviewer === true ? "good" : c.audience_agrees_with_reviewer === false ? "warn" : "neutral"}>
                  {c.audience_agrees_with_reviewer === true ? "AGREES" : c.audience_agrees_with_reviewer === false ? "DIFFERS" : "UNCLEAR"}
                </Badge>
              </div>
              <div className="mt-4 flex h-2 overflow-hidden rounded-full bg-white/[.05]" aria-label="Comment sentiment distribution">
                <div className="bg-emerald-300" style={{ width: `${c.positive_pct}%` }} />
                <div className="bg-slate-500" style={{ width: `${c.neutral_pct}%` }} />
                <div className="bg-rose-300" style={{ width: `${c.negative_pct}%` }} />
              </div>
              <div className="mt-2 flex justify-between text-[10px] font-medium text-slate-500"><span>{c.positive_pct}% positive</span><span>{c.neutral_pct}% neutral</span><span>{c.negative_pct}% negative</span></div>
            </div>
          )}
        </div>
      )}
    </article>
  );
}
