import type { ReactNode } from "react";

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "good" | "warn" | "bad" | "neutral" | "accent" }) {
  const styles = {
    good: "border-emerald-400/20 bg-emerald-400/8 text-emerald-200",
    warn: "border-amber-300/20 bg-amber-300/8 text-amber-100",
    bad: "border-rose-400/20 bg-rose-400/8 text-rose-200",
    neutral: "border-white/10 bg-white/[.035] text-slate-300",
    accent: "border-indigo-300/20 bg-indigo-300/10 text-indigo-200",
  }[tone];
  return <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold tracking-wide ${styles}`}>{children}</span>;
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-500">{children}</div>;
}

export function Divider() {
  return <div className="h-px w-full bg-white/[.07]" />;
}
