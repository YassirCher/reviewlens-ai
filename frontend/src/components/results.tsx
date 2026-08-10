import { AlertTriangle, Check, CircleX, Clock3, ShieldCheck, Sparkles, UserCheck, UserX } from "lucide-react";
import type { AnalysisResponse, Verdict } from "@/lib/types";
import { Badge, SectionLabel } from "./ui";
import { ScoreRing } from "./score-ring";
import { VideoCard } from "./video-card";

function verdictMeta(verdict: Verdict) {
  if (verdict === "buy") return { label: "Buy", tone: "good" as const, icon: Check };
  if (verdict === "buy_with_caveats") return { label: "Buy with caveats", tone: "warn" as const, icon: AlertTriangle };
  if (verdict === "mixed") return { label: "Mixed", tone: "warn" as const, icon: AlertTriangle };
  if (verdict === "do_not_buy") return { label: "Do not buy", tone: "bad" as const, icon: CircleX };
  return { label: "Unclear", tone: "neutral" as const, icon: AlertTriangle };
}

export function Results({ result }: { result: AnalysisResponse }) {
  const overall = result.overall;
  if (!overall) return null;
  const meta = verdictMeta(overall.verdict);
  const VerdictIcon = meta.icon;

  return (
    <div className="space-y-5">
      <section className="glass relative overflow-hidden rounded-[30px] p-6 sm:p-8 lg:p-9">
        <div className="pointer-events-none absolute -right-24 -top-28 h-72 w-72 rounded-full bg-indigo-400/[.08] blur-3xl" />
        <div className="relative grid items-center gap-8 lg:grid-cols-[auto_1fr_auto]">
          <ScoreRing score={overall.score} />
          <div className="min-w-0">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Badge tone={meta.tone}><VerdictIcon className="mr-1.5 h-3 w-3" />{meta.label.toUpperCase()}</Badge>
              <Badge tone="accent">{result.videos.length} SOURCES</Badge>
            </div>
            <p className="text-xs font-bold uppercase tracking-[.18em] text-slate-500">Consensus for</p>
            <h2 className="mt-1.5 text-balance text-2xl font-semibold tracking-[-.035em] text-white sm:text-3xl">{result.product_name}</h2>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">{overall.summary}</p>
          </div>
          <div className="grid min-w-[170px] grid-cols-2 gap-2 lg:grid-cols-1">
            <div className="soft-card rounded-2xl p-3.5">
              <div className="flex items-center gap-2 text-xs text-slate-500"><ShieldCheck className="h-3.5 w-3.5" /> Confidence</div>
              <div className="mt-1.5 text-xl font-semibold text-white">{overall.confidence}%</div>
            </div>
            <div className="soft-card rounded-2xl p-3.5">
              <div className="flex items-center gap-2 text-xs text-slate-500"><Sparkles className="h-3.5 w-3.5" /> Model</div>
              <div className="mt-1.5 truncate text-xs font-semibold text-slate-200" title={result.model_used}>{result.model_used}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-5 lg:grid-cols-2">
        <div className="glass rounded-[26px] p-6">
          <SectionLabel>Reviewer consensus</SectionLabel>
          <div className="grid gap-6 sm:grid-cols-2">
            <div>
              <p className="mb-3 text-sm font-semibold text-emerald-200">Strongest positives</p>
              <ul className="space-y-2.5">
                {(overall.consensus_pros.length ? overall.consensus_pros : ["No strong cross-review consensus detected."]).map((item) => <li key={item} className="flex gap-2.5 text-sm leading-5 text-slate-300"><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-300" />{item}</li>)}
              </ul>
            </div>
            <div>
              <p className="mb-3 text-sm font-semibold text-rose-200">Strongest caveats</p>
              <ul className="space-y-2.5">
                {(overall.consensus_cons.length ? overall.consensus_cons : ["No repeated negative point across sources."]).map((item) => <li key={item} className="flex gap-2.5 text-sm leading-5 text-slate-300"><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-rose-300" />{item}</li>)}
              </ul>
            </div>
          </div>
        </div>

        <div className="glass rounded-[26px] p-6">
          <SectionLabel>Evidence quality</SectionLabel>
          <div className="soft-card flex items-center gap-3 rounded-2xl p-4">
            <div className="grid h-9 w-9 place-items-center rounded-xl bg-indigo-300/10 text-indigo-200"><Clock3 className="h-4 w-4" /></div>
            <div>
              <p className="text-xs text-slate-500">Longest stated usage period</p>
              <p className="mt-1 text-sm font-semibold text-white">{overall.longest_usage_period || "Not clearly stated"}</p>
            </div>
          </div>
          {overall.disagreements.length > 0 && <div className="mt-4"><p className="mb-2 text-xs font-semibold text-amber-200">Where reviewers differ</p>{overall.disagreements.slice(0,3).map((d) => <p key={d} className="mb-2 text-sm leading-5 text-slate-400">{d}</p>)}</div>}
        </div>
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between px-1">
          <div><p className="text-sm font-semibold text-white">Source reviews</p><p className="mt-1 text-xs text-slate-500">Expand a card to inspect evidence, usage context and audience feedback.</p></div>
          {result.analyze_comments && <Badge tone="accent">COMMENTS ON</Badge>}
        </div>
        <div className="space-y-3">{result.videos.map((v, i) => <VideoCard key={v.video.video_id} analysis={v} index={i} />)}</div>
      </section>

      <section className="grid gap-5 lg:grid-cols-2">
        <div className="glass rounded-[26px] p-6">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white"><UserCheck className="h-4 w-4 text-emerald-300" /> Best fit</div>
          <ul className="space-y-2.5">{overall.who_should_buy.slice(0,5).map((item) => <li key={item} className="text-sm leading-5 text-slate-300">• {item}</li>)}</ul>
        </div>
        <div className="glass rounded-[26px] p-6">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-white"><UserX className="h-4 w-4 text-rose-300" /> Think twice if</div>
          <ul className="space-y-2.5">{overall.who_should_avoid.slice(0,5).map((item) => <li key={item} className="text-sm leading-5 text-slate-300">• {item}</li>)}</ul>
        </div>
      </section>

      {result.warnings.length > 0 && (
        <div className="rounded-2xl border border-amber-300/15 bg-amber-300/[.045] p-4">
          <div className="flex gap-3"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-200" /><div><p className="text-xs font-semibold text-amber-100">Run notes</p><ul className="mt-2 space-y-1 text-xs leading-5 text-slate-400">{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div></div>
        </div>
      )}
    </div>
  );
}
