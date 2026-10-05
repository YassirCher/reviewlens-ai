"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Check, Clock3, GitBranch, MessageSquareText, ShieldCheck, UserRoundCheck, Youtube } from "lucide-react";
import { admissionMessage, nextAvailabilityCheck, createRun, preflight, productError, V2ApiError, type AnalysisInput, type Preflight } from "@/lib/v2";

const PENDING_KEY = "reviewlens:v2:pending-submission";

type Notice = { key: string; kind: "preflight" | "creation"; text: string; retryAt: number | null };

export function V2Intake({ initialProduct = "" }: { initialProduct?: string }) {
  const router = useRouter();
  const [name, setName] = useState(initialProduct);
  const [videoCount, setVideoCount] = useState(3);
  const [comments, setComments] = useState(false);
  const [locale, setLocale] = useState<"en" | "fr">("en");
  const [confirmedName, setConfirmedName] = useState<string | null>(null);
  const [estimateRecord, setEstimate] = useState<{ key: string; data: Preflight; retryAt: number | null } | null>(null);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setMessage] = useState<Notice | null>(null);
  const [recheck, setRecheck] = useState(0);
  const [touched, setTouched] = useState(false);
  const composing = useRef(false);
  const generation = useRef(0);
  const activeCheck = useRef<AbortController | null>(null);
  const validation = productError(name);
  const input: AnalysisInput = { product_name: name.trim().replace(/\s+/g, " "), video_count: videoCount, analyze_comments: comments, locale,
    ...(confirmedName === name ? { intent_confirmation: { product_name: name.trim().replace(/\s+/g, " "), exact_model: true as const } } : {}) };
  const inputKey = JSON.stringify(input);
  const estimate = estimateRecord?.key === inputKey ? estimateRecord.data : null;
  const message = notice?.key === inputKey ? notice : null;
  const retryAt = message?.retryAt ?? (estimateRecord?.key === inputKey ? estimateRecord.retryAt : null);

  useEffect(() => {
    if (retryAt === null || submitting) return;
    const timer = setTimeout(() => setRecheck(value => value + 1), Math.min(2_147_483_647, Math.max(1000, retryAt - Date.now())));
    return () => clearTimeout(timer);
  }, [retryAt, submitting]);

  useEffect(() => {
    if (productError(name)) return;
    const controller = new AbortController();
    activeCheck.current = controller;
    const request = ++generation.current;
    const timer = setTimeout(() => {
      setChecking(true);
      preflight(input, controller.signal).then(data => {
        if (controller.signal.aborted || request !== generation.current) return;
        setEstimate({ key: inputKey, data, retryAt: data.allowed ? null : nextAvailabilityCheck(data.recovery) });
        setMessage(current => current?.kind === "preflight" ? null : current);
      }).catch((error) => {
        if (controller.signal.aborted || request !== generation.current || error?.name === "AbortError") return;
        setMessage({ key: inputKey, kind: "preflight",
          text: error instanceof V2ApiError ? admissionMessage(error.code, error.recovery, error.message) : "Cannot check availability right now.",
          retryAt: error instanceof V2ApiError ? nextAvailabilityCheck(error.recovery, error.retryAfter) : null });
      }).finally(() => { if (!controller.signal.aborted && request === generation.current) setChecking(false); });
    }, 550);
    return () => { clearTimeout(timer); controller.abort(); };
    // Depend on the serialized, normalized form rather than object identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inputKey, recheck]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (composing.current || submitting) return;
    setTouched(true);
    if (validation) return;
    setSubmitting(true);
    ++generation.current;
    activeCheck.current?.abort();
    setChecking(false);
    setMessage(null);
    let kind: Notice["kind"] = "preflight";
    try {
      const current = await preflight(input);
      setEstimate({ key: inputKey, data: current, retryAt: current.allowed ? null : nextAvailabilityCheck(current.recovery) });
      if (!current.allowed) return;
      kind = "creation";
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
      setMessage({ key: inputKey, kind,
        text: error instanceof V2ApiError ? admissionMessage(error.code, error.recovery, error.message) : "Connection interrupted. Retry to resume the same submission.",
        retryAt: error instanceof V2ApiError ? nextAvailabilityCheck(error.recovery, error.retryAfter) : null });
    } finally { setSubmitting(false); }
  }

  return <>
    <section className="v2-hero" aria-labelledby="v2-hero-title">
      <div className="v2-hero-grid" aria-hidden="true" />
      <div className="v2-hero-content">
        <span className="v2-kicker">
          <span className="v2-kicker-dot" /> CLEARER PRODUCT DECISIONS
        </span>
        <h1 id="v2-hero-title">
          See the <span className="v2-hero-gradient">buying signal</span>
          <span className="v2-hero-subhead">behind the reviews.</span>
        </h1>
        <p>Compare relevant YouTube reviews through timestamped evidence. See where reviewers agree, where they differ, and how much confidence the sources deserve.</p>
        <p className="v2-access-note">
          <UserRoundCheck size={16} aria-hidden="true" />
          <span>No researcher account required · Session-owned runs</span>
        </p>
      </div>
      <div className="v2-hero-aside" aria-hidden="true">
        <div className="v2-hero-aside-top">
          <span className="v2-hero-aside-title">
            <span className="v2-pulse-indicator" /> EVIDENCE PIPELINE
          </span>
          <span className="v2-hero-aside-badge">01 / 03</span>
        </div>
        <div className="v2-hero-pipeline">
          <div className="v2-pipeline-step active">
            <span className="v2-step-icon">1</span>
            <span>Intake Product</span>
            <span className="v2-pipeline-arrow">ACTIVE</span>
          </div>
          <div className="v2-pipeline-step">
            <span className="v2-step-icon">2</span>
            <span>Corroborate Sources</span>
          </div>
          <div className="v2-pipeline-step">
            <span className="v2-step-icon">3</span>
            <span>Synthesize Verdict</span>
          </div>
        </div>
      </div>
    </section>

    <section className="v2-composer" aria-labelledby="v2-composer-title">
      <div className="v2-section-top"><div><p className="v2-eyebrow">START A RESEARCH RUN</p><h2 id="v2-composer-title">What are you considering?</h2></div><span className="v2-step">01 — PRODUCT</span></div>
      <form onSubmit={submit} noValidate>
        <label className="v2-label" htmlFor="v2-product">Product name or exact model</label>
        <div className="v2-input-row"><input id="v2-product" autoComplete="off" value={name} disabled={submitting} maxLength={500} aria-invalid={Boolean(touched && validation)} aria-describedby="v2-product-help v2-product-error" placeholder="e.g. Sony WH-1000XM5 headphones" onChange={(event) => { setName(event.target.value); setConfirmedName(null); setEstimate(null); setChecking(false); setMessage(null); }} onBlur={() => setTouched(true)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} /><button className="v2-button v2-submit" type="submit" disabled={submitting || Boolean(validation)}>{submitting ? "Starting research…" : "Analyze product"}<ArrowRight size={18} aria-hidden="true" /></button></div>
        <p id="v2-product-help" className="v2-help">Include the brand and model for better source matching. For example: “POCO F7” or “Dyson V15 Detect”.</p>
        <p id="v2-product-error" className="v2-error" role="alert">{touched && validation ? validation : ""}</p>

        {estimate?.intent_resolution?.status === "requires_clarification" && <section className="v2-clarification" aria-labelledby="v2-intent-heading">
          <h3 id="v2-intent-heading">Which product do you mean?</h3>
          <p>{estimate.intent_resolution.question}</p>
          <p className="v2-help">Update the product field with the exact model, then select Analyze product. No research has started.</p>
          {estimate.intent_resolution.reason === "unknown_model" && <label className="v2-switch-label">
            <span>This is the complete brand and model name</span><input type="checkbox" checked={confirmedName === name} disabled={submitting} onChange={event => { setConfirmedName(event.target.checked ? name : null); setEstimate(null); setMessage(null); }} />
          </label>}
        </section>}
        <details className="v2-advanced"><summary>Research options <span>Choose how many reviews to compare and whether to include comments</span></summary><div className="v2-option-grid"><div><label className="v2-label" htmlFor="v2-video-count">Review sources</label><select id="v2-video-count" value={videoCount} disabled={submitting} onChange={(event) => { if (Number(event.target.value) === videoCount) return; setVideoCount(Number(event.target.value)); setEstimate(null); setChecking(false); setMessage(null); }}>{[3,4,5].map(count => <option key={count} value={count}>{count} videos{count === 3 ? " · default" : ""}</option>)}</select><p className="v2-help">More sources can improve coverage but take longer and use more analysis capacity.</p></div><div><label className="v2-switch-label" htmlFor="v2-comments"><span><MessageSquareText size={18} aria-hidden="true" /> Include top comments</span><input id="v2-comments" type="checkbox" checked={comments} disabled={submitting} onChange={(event) => { setComments(event.target.checked); setEstimate(null); setChecking(false); setMessage(null); }} /></label><p className="v2-help">Comments are secondary signals, never a substitute for independent reviews.</p></div><div><label className="v2-label" htmlFor="v2-report-language">Evidence language</label><select id="v2-report-language" value={locale} disabled={submitting} onChange={event => { setLocale(event.target.value as "en" | "fr"); setEstimate(null); setMessage(null); }}><option value="en">English</option><option value="fr">French</option></select><p className="v2-help">Comments in other languages are translated for analysis; originals remain available.</p></div></div></details>
        <div className="v2-admission" aria-live="polite">{checking ? <><Clock3 size={16} aria-hidden="true" /> Checking availability…</> : estimate && !validation ? <>{estimate.allowed ? <Check size={16} aria-hidden="true" /> : <ShieldCheck size={16} aria-hidden="true" />}<span>{estimate.allowed ? `Research available · ${estimate.remaining_public_quota.hourly_remaining} hourly request${estimate.remaining_public_quota.hourly_remaining === 1 ? "" : "s"} remaining · estimate is non-binding` : admissionMessage(estimate.denial_code, estimate.recovery)}</span></> : <><ShieldCheck size={16} aria-hidden="true" /> Availability and quota are checked before research begins.</>}</div>
        {message && <p className="v2-alert v2-alert-error" role="alert">{message.text}</p>}
      </form>
    </section>

    <section className="v2-trust" aria-label="What you can inspect"><div><Youtube size={22} aria-hidden="true" /><strong>Timestamped evidence</strong><span>Follow short excerpts back to the original video.</span></div><div><GitBranch size={22} aria-hidden="true" /><strong>Independent comparison</strong><span>See recurring findings and meaningful disagreements.</span></div><div><ShieldCheck size={22} aria-hidden="true" /><strong>Inspect the conclusion</strong><span>Understand coverage, limitations, and confidence.</span></div></section>

    <section id="how-it-works" className="v2-how"><p className="v2-eyebrow">HOW IT WORKS</p><h2>From source to signal.</h2><p>ReviewLens finds relevant videos, acquires available captions, checks claims against short excerpts, and audits the report before sharing it. Missing sources and disagreements stay visible.</p><div className="v2-how-steps"><div><span>01</span><strong>Find relevant reviews</strong><p>Filter noise and select diverse sources.</p></div><div><span>02</span><strong>Trace the evidence</strong><p>Connect conclusions to timestamped moments.</p></div><div><span>03</span><strong>Weigh the decision</strong><p>Show a score, confidence, caveats, and coverage.</p></div></div></section>
  </>;
}
