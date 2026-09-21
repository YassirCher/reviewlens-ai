"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, Check, CircleAlert, CircleDashed, RotateCcw, Square, Wifi, WifiOff } from "lucide-react";
import { cancelRun, getRun, readEvents, reportTokenFromApiPath, validRunId, V2ApiError, type PublicTask, type RunStatus } from "@/lib/v2";

const TERMINAL = new Set(["complete", "partial", "failed", "cancelled"]);
const STAGES = [
  { name: "Discover", keys: ["validate_request", "plan_research", "discover_candidates", "curate_sources"] },
  { name: "Acquire", keys: ["fetch_transcript", "fetch_comments"] },
  { name: "Analyze", keys: ["analyze_review", "analyze_audience"] },
  { name: "Connect", keys: ["curate_knowledge"] },
  { name: "Decide", keys: ["build_consensus", "correct_consensus"] },
  { name: "Verify", keys: ["audit_report", "reaudit_report", "publish_report"] },
];

function stageState(tasks: PublicTask[]): string {
  if (!tasks.length) return "queued";
  if (tasks.some(task => task.status === "running" || task.status === "cancelling")) return "running";
  if (tasks.some(task => task.status === "failed" || task.status === "timed_out")) return "warning";
  if (tasks.every(task => ["succeeded", "skipped", "cancelled"].includes(task.status))) return tasks.some(task => task.status !== "succeeded") ? "warning" : "succeeded";
  return "queued";
}

function elapsed(start: string, endOrNow: string | number): string {
  const endTime = typeof endOrNow === "string" ? new Date(endOrNow).getTime() : endOrNow;
  const startTime = new Date(start).getTime();
  if (isNaN(startTime) || isNaN(endTime)) return "0m 00s";
  const seconds = Math.max(0, Math.floor((endTime - startTime) / 1000));
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}
function runDate(value: string): string { return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }

export function V2Progress({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState<"connecting" | "live" | "reconnecting" | "polling" | "closed">("connecting");
  const [announcement, setAnnouncement] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [cancelling, setCancelling] = useState(false);
  const [retrySerial, setRetrySerial] = useState(0);
  const sequence = useRef(0);
  const runRef = useRef<RunStatus | null>(null);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const state = await getRun(runId, signal);
    runRef.current = state;
    setRun(state);
    setError(null);
    if (state.progress_sequence > sequence.current) {
      sequence.current = state.progress_sequence;
      sessionStorage.setItem(`reviewlens:v2:sequence:${runId}`, String(sequence.current));
    }
    return state;
  }, [runId]);

  useEffect(() => {
    if (!validRunId(runId)) return;
    const controller = new AbortController();
    let stopped = false;
    const saved = Number(sessionStorage.getItem(`reviewlens:v2:sequence:${runId}`));
    sequence.current = Number.isSafeInteger(saved) && saved >= 0 ? saved : 0;
    const clock = setInterval(() => {
      if (TERMINAL.has(runRef.current?.status || "")) return;
      setNow(Date.now());
    }, 1000);
    const poll = setInterval(() => {
      if (stopped || TERMINAL.has(runRef.current?.status || "")) return;
      refresh(controller.signal).catch(() => setConnection(current => current === "live" ? "live" : "polling"));
    }, 5000);
    async function monitor() {
      try {
        const initial = await refresh(controller.signal);
        if (TERMINAL.has(initial.status)) { setConnection("closed"); return; }
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof V2ApiError && reason.status === 404 ? "Analysis not found or access expired." : "Could not load this analysis. Retry the connection.");
        return;
      }
      let failures = 0;
      while (!stopped && !TERMINAL.has(runRef.current?.status || "")) {
        try {
          await readEvents(runId, sequence.current, (name, event) => {
            if (event.sequence <= sequence.current) return;
            sequence.current = event.sequence;
            sessionStorage.setItem(`reviewlens:v2:sequence:${runId}`, String(sequence.current));
            if (name !== "task.progress") setAnnouncement(event.label);
            if (name.startsWith("run.") || name === "task.failed" || name === "source.completed") refresh(controller.signal).catch(() => {});
          }, controller.signal, () => setConnection("live"));
          if (stopped) return;
          const latest = await refresh(controller.signal);
          if (TERMINAL.has(latest.status)) { setConnection("closed"); return; }
          setConnection("reconnecting");
        } catch (reason) {
          if (controller.signal.aborted) return;
          if (reason instanceof V2ApiError && reason.status === 404) { setError("Analysis not found or access expired."); return; }
          setConnection("reconnecting");
        }
        failures += 1;
        await new Promise<void>(resolve => setTimeout(resolve, Math.min(8000, 1000 * 2 ** Math.min(failures, 3))));
      }
    }
    void monitor();
    return () => { stopped = true; controller.abort(); clearInterval(clock); clearInterval(poll); };
  }, [runId, refresh, retrySerial]);

  async function cancel() {
    if (!run || cancelling) return;
    if (run.total_tokens > 0 && !window.confirm("Cancel this analysis? Work already completed will not become a public report.")) return;
    setCancelling(true);
    try { await cancelRun(runId); await refresh(); setAnnouncement("Cancellation requested."); }
    catch (reason) { setError(reason instanceof V2ApiError ? reason.message : "Cancellation could not be requested."); }
    finally { setCancelling(false); }
  }

  if ((!validRunId(runId) || error) && !run) return <section className="v2-empty"><CircleAlert size={30} aria-hidden="true" /><h1>Analysis unavailable</h1><p>{error || "Analysis not found."}</p>{validRunId(runId) && <button className="v2-button" onClick={() => { setError(null); setRetrySerial(value => value + 1); }}>Retry connection</button>}</section>;
  if (!run) return <section className="v2-empty" aria-busy="true"><CircleDashed className="v2-spin" size={30} aria-hidden="true" /><h1>Opening your research run</h1><p>Loading committed progress from the server…</p></section>;

  const token = reportTokenFromApiPath(run.report_url);
  const isActive = !TERMINAL.has(run.status);
  const headingLabel = isActive ? "RESEARCH IN PROGRESS" : run.status === "failed" ? "RESEARCH STOPPED" : "RESEARCH FINISHED";
  const connectionLabel = connection === "live" ? "Live updates connected"
    : connection === "closed" && run.status === "failed" ? "Run ended with an error"
    : connection === "closed" && run.status === "cancelled" ? "Run was cancelled"
    : connection === "closed" ? "Run finished"
    : connection === "polling" ? "Polling for updates"
    : "Reconnecting — work continues in the background";
  const sourceTasks = run.tasks.filter(task => /^(fetch_transcript|fetch_comments|analyze_review|analyze_audience)\.source_\d+$/.test(task.task_key));
  const slots = Array.from(new Set(sourceTasks.map(task => Number(task.task_key.match(/source_(\d+)$/)?.[1])))).sort((a,b) => a-b);
  const terminalEnd = run.completed_at || (run.tasks?.length ? run.tasks.map(t => t.completed_at).filter((t): t is string => Boolean(t)).sort().pop() : null);
  const durationText = !isActive && terminalEnd
    ? elapsed(run.started_at || run.created_at, terminalEnd)
    : elapsed(run.started_at || run.created_at, now);
  return <>
    <div className="v2-page-heading"><Link className="v2-back" href="/">← New research</Link><p className="v2-eyebrow">{headingLabel}</p><h1>{run.product_name}</h1><p>Started {runDate(run.created_at)} UTC · {isActive ? "Elapsed" : "Duration"} {durationText}</p></div>
    {run.status === "partial" && <div className="v2-alert v2-alert-warning v2-run-notice" role="status">Partial report: {run.source_count_analyzed} of {run.source_count_requested} requested sources could be analyzed. Valid evidence remains available.</div>}
    {run.status === "failed" && <div className="v2-alert v2-alert-error v2-run-notice" role="alert">{run.failure?.message || "The analysis could not be completed."} <Link href="/">Start a new research run</Link>.</div>}
    {run.status === "cancelled" && <div className="v2-alert v2-alert-warning v2-run-notice" role="status">This analysis was cancelled. No public report was published.</div>}
    <div className="v2-progress-layout"><section className="v2-panel v2-progress-main" aria-labelledby="v2-timeline-heading"><div className="v2-panel-heading"><div><p className="v2-eyebrow">LIVE WORKFLOW</p><h2 id="v2-timeline-heading">Research timeline</h2></div><span className={`v2-status v2-status-${run.status}`}>{run.status.replaceAll("_", " ")}</span></div>
      <div className="v2-connection" role="status">{connection === "live" ? <Wifi size={16} /> : <WifiOff size={16} />}{connectionLabel}</div>
      <ol className="v2-timeline">{STAGES.map((stage, index) => { const tasks = run.tasks.filter(task => stage.keys.some(key => task.task_key === key || task.task_key.startsWith(`${key}.`))); const state = stageState(tasks); return <li key={stage.name} className={`v2-timeline-item v2-timeline-${state}`}><span className="v2-timeline-index">{String(index + 1).padStart(2,"0")}</span><div><div className="v2-timeline-title"><strong>{stage.name}</strong><span>{state}</span></div><p>{tasks.length ? `${tasks.filter(task => ["succeeded","failed","skipped","cancelled","timed_out"].includes(task.status)).length} of ${tasks.length} tasks terminal` : "Waiting for earlier work"}</p></div>{state === "succeeded" ? <Check size={18} aria-hidden="true" /> : state === "running" ? <CircleDashed size={18} aria-hidden="true" /> : null}</li>; })}</ol>
      {slots.length > 0 && <div className="v2-source-progress"><h3>Source progress</h3><ul>{slots.map(slot => { const tasks = sourceTasks.filter(task => task.task_key.endsWith(`source_${slot}`)); return <li key={slot}><span>Source {slot}</span><span className="v2-status">{stageState(tasks)}</span></li>; })}</ul></div>}
    </section>
    <aside className="v2-panel v2-progress-aside"><p className="v2-eyebrow">RUN SNAPSHOT</p><h2>What’s happening</h2><div className="v2-metric"><strong>{run.completed_tasks} / {run.total_tasks}</strong><span>tasks finished</span></div><div className="v2-metric"><strong>{run.source_count_analyzed} / {run.source_count_requested}</strong><span>sources analyzed</span></div><div className="v2-metric"><strong>{run.total_tokens.toLocaleString()}</strong><span>tokens recorded{run.usage_pending ? " · accounting pending" : ""}</span></div>
      {isActive ? <><p className="v2-aside-note">Research continues if you leave this page. Return with the same browser session to follow it.</p><button className="v2-button-secondary" onClick={() => { setRetrySerial(value => value + 1); }}><RotateCcw size={16} aria-hidden="true" /> Retry connection</button><button className="v2-button-danger" disabled={cancelling || run.status === "cancelling"} onClick={cancel}><Square size={14} aria-hidden="true" />{cancelling || run.status === "cancelling" ? "Cancelling…" : "Cancel analysis"}</button></> : null}
      {token && <Link className="v2-button" href={`/r/${token}`}>Open report <ArrowRight size={17} aria-hidden="true" /></Link>}
    </aside></div>
    {run.warnings.length > 0 && <section className="v2-panel v2-warnings"><h2>Research notes</h2><ul>{run.warnings.map((warning, index) => <li key={`${warning}-${index}`}>{warning}</li>)}</ul></section>}
    <p className="v2-sr-only" aria-live="polite" aria-atomic="true">{announcement}</p>
    {error && <div className="v2-alert v2-alert-error" role="alert">{error}</div>}
  </>;
}
