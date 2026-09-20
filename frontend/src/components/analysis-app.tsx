"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, Bot, Braces, Check, CircleHelp, Github, MessageSquareText, Search, ShieldCheck, Sparkles, Youtube } from "lucide-react";
import { analyzeProduct, getConfig } from "@/lib/api";
import type { AnalysisResponse, AppConfig, ProgressState, ProviderChoice } from "@/lib/types";
import { Badge } from "./ui";
import { ProgressPanel } from "./progress-panel";
import { Results } from "./results";

const initialProgress: ProgressState = { stage: "idle", label: "Ready", percent: 0, detail: "" };

export function AnalysisApp() {
  const [product, setProduct] = useState("");
  const [comments, setComments] = useState(false);
  const [provider, setProvider] = useState<ProviderChoice>("auto");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [progress, setProgress] = useState<ProgressState>(initialProgress);
  const [result, setResult] = useState<AnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getConfig().then(setConfig).catch(() => setConfig(null));
    return () => abortRef.current?.abort();
  }, []);

  const availableProviders = useMemo(() => config?.providers.filter((p) => p.available) || [], [config]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const clean = product.trim();
    if (clean.length < 2 || running) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    setError(null);
    setResult(null);
    setProgress({ stage: "searching", label: "Starting analysis", percent: 3, detail: clean });
    try {
      const response = await analyzeProduct(clean, comments, provider, { onProgress: setProgress }, controller.signal);
      setResult(response);
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError((err as Error).message || "Analysis failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className="relative min-h-screen overflow-hidden">
      <div className="bg-grid pointer-events-none absolute inset-x-0 top-0 h-[760px] opacity-70" />
      <div className="relative mx-auto w-full max-w-[1180px] px-4 pb-20 sm:px-6 lg:px-8">
        <nav className="flex h-20 items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="grid h-9 w-9 place-items-center rounded-xl border border-indigo-300/20 bg-gradient-to-br from-indigo-300/15 to-emerald-300/10 shadow-[inset_0_1px_0_rgba(255,255,255,.08)]"><Braces className="h-4 w-4 text-indigo-200" /></div>
            <div><div className="text-sm font-semibold tracking-[-.02em] text-white">ReviewLens</div><div className="text-[9px] font-bold uppercase tracking-[.22em] text-slate-600">Product Intelligence</div></div>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/research" className="text-[10px] text-indigo-200 transition hover:text-white sm:text-xs">Open current research experience →</Link>
            <div className="hidden items-center gap-2 rounded-full border border-white/[.07] bg-white/[.025] px-3 py-1.5 text-[11px] text-slate-500 sm:flex">
              <span className={`h-1.5 w-1.5 rounded-full ${config && availableProviders.length ? "bg-emerald-300" : "bg-amber-300"}`} />
              {config ? `${availableProviders.length} AI provider${availableProviders.length === 1 ? "" : "s"} ready` : "API status unknown"}
            </div>
            <a href="#how-it-works" className="grid h-9 w-9 place-items-center rounded-xl border border-white/[.07] bg-white/[.025] text-slate-500 transition hover:text-white" aria-label="How it works"><CircleHelp className="h-4 w-4" /></a>
          </div>
        </nav>

        {!result && !running && (
          <header className="mx-auto max-w-4xl pb-10 pt-16 text-center sm:pt-24">
            <Badge tone="accent"><Sparkles className="mr-1.5 h-3 w-3" /> EVIDENCE-BACKED BUYING SIGNALS</Badge>
            <h1 className="mt-6 text-balance text-4xl font-semibold leading-[1.03] tracking-[-.055em] text-white sm:text-6xl lg:text-[68px]">Three reviews in. <span className="text-slate-500">One clear decision out.</span></h1>
            <p className="mx-auto mt-6 max-w-2xl text-balance text-base leading-7 text-slate-400">ReviewLens reads the transcripts behind the most-viewed relevant YouTube reviews, surfaces how long reviewers actually used the product, and turns their evidence into a transparent buy signal.</p>
          </header>
        )}

        <section className={`${result || running ? "pt-8" : "mx-auto max-w-4xl"}`}>
          <form onSubmit={submit} className="glass premium-input rounded-[28px] p-3 transition sm:p-3.5">
            <div className="flex flex-col gap-3 sm:flex-row">
              <div className="flex min-h-14 flex-1 items-center gap-3 rounded-2xl bg-black/15 px-4">
                <Search className="h-4 w-4 shrink-0 text-slate-500" />
                <input value={product} onChange={(e) => setProduct(e.target.value)} maxLength={200} placeholder="Search a product — POCO F7, RTX 5070, Dyson V15..." className="h-14 min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-slate-600" disabled={running} />
              </div>
              <button disabled={running || product.trim().length < 2} className="group flex h-14 items-center justify-center gap-2 rounded-2xl bg-white px-5 text-sm font-semibold text-slate-950 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40 sm:min-w-36">
                {running ? "Analyzing" : "Analyze"}<ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </button>
            </div>

            <div className="mt-2 flex flex-col gap-2 px-1 pb-1 sm:flex-row sm:items-center sm:justify-between">
              <label className="flex cursor-pointer items-center gap-2.5 rounded-xl px-2 py-2 text-xs text-slate-400 transition hover:bg-white/[.025]">
                <button type="button" onClick={() => setComments((v) => !v)} disabled={running} className={`relative h-5 w-9 rounded-full border transition ${comments ? "border-indigo-300/40 bg-indigo-400/30" : "border-white/10 bg-white/[.04]"}`} aria-pressed={comments}>
                  <span className={`absolute top-[3px] h-3 w-3 rounded-full transition ${comments ? "left-[18px] bg-indigo-100" : "left-[3px] bg-slate-500"}`} />
                </button>
                <MessageSquareText className="h-3.5 w-3.5" /> Analyze top comments
                <span className="hidden text-slate-600 md:inline">(same AI call)</span>
              </label>

              <div className="flex items-center gap-2 px-2">
                <Bot className="h-3.5 w-3.5 text-slate-600" />
                <select value={provider} onChange={(e) => setProvider(e.target.value as ProviderChoice)} disabled={running} className="bg-transparent text-xs font-medium text-slate-400 outline-none">
                  <option value="auto" className="bg-[#11151d]">Auto provider</option>
                  {config?.providers.map((p) => <option key={p.id} value={p.id} disabled={!p.available} className="bg-[#11151d]">{p.label}{p.free_friendly ? " · free" : ""}{!p.available ? " · not configured" : ""}</option>)}
                </select>
              </div>
            </div>
          </form>
          {!running && !result && <div className="mt-3 flex flex-wrap justify-center gap-x-5 gap-y-2 text-[11px] text-slate-600"><span className="flex items-center gap-1.5"><Check className="h-3 w-3 text-emerald-400/70" /> Top 3 relevant reviews</span><span className="flex items-center gap-1.5"><Check className="h-3 w-3 text-emerald-400/70" /> Timestamped evidence</span><span className="flex items-center gap-1.5"><Check className="h-3 w-3 text-emerald-400/70" /> Optional audience pulse</span></div>}
        </section>

        {error && <div className="mx-auto mt-5 max-w-4xl rounded-2xl border border-rose-400/20 bg-rose-400/[.06] p-4 text-sm leading-6 text-rose-100"><strong>Analysis failed.</strong> {error}</div>}

        {running && <div className="mt-5"><ProgressPanel progress={progress} /></div>}
        {result && <div className="mt-5"><Results result={result} /></div>}

        {!result && !running && (
          <section id="how-it-works" className="mt-24 border-t border-white/[.06] pt-16 sm:mt-32">
            <div className="mb-8 max-w-xl"><p className="text-xs font-bold uppercase tracking-[.18em] text-indigo-300/70">How it works</p><h2 className="mt-3 text-2xl font-semibold tracking-[-.035em] text-white">A small pipeline, not a black box.</h2><p className="mt-3 text-sm leading-6 text-slate-500">The model never browses freely. ReviewLens controls discovery, captions, optional comments and source ordering, then asks AI to interpret only the supplied evidence.</p></div>
            <div className="grid gap-3 md:grid-cols-3">
              {[
                [Youtube, "01 · Discover", "Search captioned review candidates, filter obvious noise, then prioritize the highest-view relevant sources."],
                [ShieldCheck, "02 · Extract", "Keep timestamped transcript evidence and capture stated usage periods instead of pretending every review is long-term."],
                [Sparkles, "03 · Decide", "Score each review separately, show disagreements, then generate an overall recommendation with confidence."],
              ].map(([Icon, title, text]) => {
                const I = Icon as typeof Youtube;
                return <div key={String(title)} className="soft-card rounded-[24px] p-5"><I className="h-5 w-5 text-indigo-300" /><p className="mt-5 text-sm font-semibold text-white">{String(title)}</p><p className="mt-2 text-sm leading-6 text-slate-500">{String(text)}</p></div>;
              })}
            </div>
          </section>
        )}

        <footer className="mt-20 flex flex-col gap-3 border-t border-white/[.06] py-7 text-[11px] text-slate-600 sm:flex-row sm:items-center sm:justify-between">
          <span>ReviewLens POC · Source-grounded product research</span>
          <span className="flex items-center gap-4"><span className="flex items-center gap-1.5"><ShieldCheck className="h-3 w-3" /> Keys stay server-side</span><span className="flex items-center gap-1.5"><Github className="h-3 w-3" /> Full source included</span></span>
        </footer>
      </div>
    </main>
  );
}
