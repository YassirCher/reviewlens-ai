"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Clock3, GitBranch, MessageSquareText, ShieldCheck, UserRoundCheck, Youtube } from "lucide-react";
import { createRun, preflight, productError, V2ApiError, type AnalysisInput, type Preflight } from "@/lib/v2";

const PENDING_KEY = "reviewlens:v2:pending-submission";

function denial(code: string | null): string {
  if (code === "queue_full") return "The research queue is full. Please try again shortly.";
  if (code === "public_daily_budget_exceeded") return "Today's research capacity has been reached. Try again tomorrow.";
  if (code?.includes("rate") || code?.includes("limit")) return "Your research limit has been reached. Please try again later.";
  if (code === "public_analysis_disabled") return "New research is temporarily paused.";
  return "Research is temporarily unavailable. Please try again later.";
}

export function V2Intake() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [videoCount, setVideoCount] = useState(5);
  const [comments, setComments] = useState(false);
  const [estimate, setEstimate] = useState<Preflight | null>(null);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const composing = useRef(false);
  const validation = productError(name);
  const input: AnalysisInput = { product_name: name.trim().replace(/\s+/g, " "), video_count: videoCount, analyze_comments: comments };
  const inputKey = JSON.stringify(input);

  useEffect(() => {
    if (productError(name)) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      setChecking(true);
      preflight(input, controller.signal).then(setEstimate).catch((error) => {
        if (error?.name !== "AbortError") setMessage(error instanceof V2ApiError ? error.message : "Cannot check availability right now.");
      }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    }, 550);
    return () => { clearTimeout(timer); controller.abort(); };
    // Depend on the serialized, normalized form rather than object identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputKey]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (composing.current || submitting) return;
    setTouched(true);
    if (validation) return;
    setSubmitting(true);
    setMessage(null);
    try {
      const current = await preflight(input);
      setEstimate(current);
      if (!current.allowed) { setMessage(denial(current.denial_code)); return; }
      let pending: { input: AnalysisInput; key: string } | null = null;
      try { pending = JSON.parse(sessionStorage.getItem(PENDING_KEY) || "null"); } catch { /* Ignore invalid local state. */ }
      const key = pending && JSON.stringify(pending.input) === inputKey && /^[A-Za-z0-9._~-]{16,160}$/.test(pending.key)
        ? pending.key : crypto.randomUUID();
      sessionStorage.setItem(PENDING_KEY, JSON.stringify({ input, key }));
      const created = await createRun(input, key);
      sessionStorage.removeItem(PENDING_KEY);
      router.push(`/analysis/${created.run_id}`);
    } catch (error) {
      if (error instanceof V2ApiError && error.status === 409) sessionStorage.removeItem(PENDING_KEY);
      setMessage(error instanceof V2ApiError ? error.message : "Connection interrupted. Retry to resume the same submission.");
    } finally { setSubmitting(false); }
  }

  return <>
    <section className="v2-hero" aria-labelledby="v2-hero-title">
      <div className="v2-hero-grid" aria-hidden="true" />
      <div className="v2-hero-content"><span className="v2-kicker"><span className="v2-kicker-line" /> CLEARER PRODUCT DECISIONS</span>
        <h1 id="v2-hero-title">See the buying signal<br /><span>behind the reviews.</span></h1>
        <p>Compare relevant YouTube reviews through timestamped evidence. See where reviewers agree, where they differ, and how much confidence the sources deserve.</p>
        <p className="v2-access-note"><UserRoundCheck size={17} aria-hidden="true" /> No researcher account required. This browser session securely owns your runs.</p>
      </div>
      <div className="v2-hero-aside" aria-hidden="true"><div className="v2-aside-number">01 / 03</div><div className="v2-aside-rule" /><p>DISCOVER <span>→</span> EXAMINE <span>→</span> DECIDE</p></div>
    </section>

    <section className="v2-composer" aria-labelledby="v2-composer-title">
      <div className="v2-section-top"><div><p className="v2-eyebrow">START A RESEARCH RUN</p><h2 id="v2-composer-title">What are you considering?</h2></div><span className="v2-step">01 — PRODUCT</span></div>
      <form onSubmit={submit} noValidate>
        <label className="v2-label" htmlFor="v2-product">Product name or exact model</label>
        <div className="v2-input-row"><input id="v2-product" autoComplete="off" value={name} maxLength={500} aria-invalid={Boolean(touched && validation)} aria-describedby="v2-product-help v2-product-error" placeholder="e.g. Sony WH-1000XM5 headphones" onChange={(event) => { setName(event.target.value); setEstimate(null); setChecking(false); setMessage(null); }} onBlur={() => setTouched(true)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} /><button className="v2-button v2-submit" type="submit" disabled={submitting || Boolean(validation)}>{submitting ? "Starting research…" : "Analyze product"}<ArrowRight size={18} aria-hidden="true" /></button></div>
        <p id="v2-product-help" className="v2-help">Include the brand and model for better source matching. For example: “POCO F7” or “Dyson V15 Detect”.</p>
        <p id="v2-product-error" className="v2-error" role="alert">{touched && validation ? validation : ""}</p>

        <details className="v2-advanced"><summary>Research options <span>Choose how many reviews to compare and whether to include comments</span></summary><div className="v2-option-grid"><div><label className="v2-label" htmlFor="v2-video-count">Review sources</label><select id="v2-video-count" value={videoCount} onChange={(event) => { setVideoCount(Number(event.target.value)); setEstimate(null); setChecking(false); }}>{[3,4,5,6,7,8].map(count => <option key={count} value={count}>{count} videos{count === 5 ? " · default" : ""}</option>)}</select><p className="v2-help">More sources can improve coverage but take longer and use more analysis capacity.</p></div><div><label className="v2-switch-label" htmlFor="v2-comments"><span><MessageSquareText size={18} aria-hidden="true" /> Include top comments</span><input id="v2-comments" type="checkbox" checked={comments} onChange={(event) => { setComments(event.target.checked); setEstimate(null); setChecking(false); }} /></label><p className="v2-help">Comments are secondary signals, never a substitute for independent reviews.</p></div></div></details>
        <div className="v2-admission" aria-live="polite">{checking ? <><Clock3 size={16} aria-hidden="true" /> Checking availability…</> : estimate && !validation ? <>{estimate.allowed ? <Check size={16} aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}<span>{estimate.allowed ? `Research available · ${estimate.remaining_public_quota.hourly_remaining} hourly request${estimate.remaining_public_quota.hourly_remaining === 1 ? "" : "s"} remaining · estimate is non-binding` : denial(estimate.denial_code)}</span></> : <><ShieldCheck size={16} aria-hidden="true" /> Availability and quota are checked before research begins.</>}</div>
        {message && <p className="v2-alert v2-alert-error" role="alert">{message}</p>}
      </form>
    </section>

    <section className="v2-trust" aria-label="What you can inspect"><div><Youtube size={22} aria-hidden="true" /><strong>Timestamped evidence</strong><span>Follow short excerpts back to the original video.</span></div><div><GitBranch size={22} aria-hidden="true" /><strong>Independent comparison</strong><span>See recurring findings and meaningful disagreements.</span></div><div><ShieldCheck size={22} aria-hidden="true" /><strong>Inspect the conclusion</strong><span>Understand coverage, limitations, and confidence.</span></div></section>

    <section id="how-it-works" className="v2-how"><p className="v2-eyebrow">HOW IT WORKS</p><h2>From source to signal.</h2><p>ReviewLens finds relevant videos, acquires available captions, checks claims against short excerpts, and audits the report before sharing it. Missing sources and disagreements stay visible.</p><div className="v2-how-steps"><div><span>01</span><strong>Find relevant reviews</strong><p>Filter noise and select diverse sources.</p></div><div><span>02</span><strong>Trace the evidence</strong><p>Connect conclusions to timestamped moments.</p></div><div><span>03</span><strong>Weigh the decision</strong><p>Show a score, confidence, caveats, and coverage.</p></div></div></section>
  </>;
}
