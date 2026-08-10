import { Check, LoaderCircle, Search, Sparkles, Subtitles, Youtube } from "lucide-react";
import type { ProgressState } from "@/lib/types";

const steps = [
  { key: "searching", label: "Discover", Icon: Search, threshold: 8 },
  { key: "transcripts", label: "Transcripts", Icon: Subtitles, threshold: 20 },
  { key: "video", label: "Reviews", Icon: Youtube, threshold: 28 },
  { key: "consensus", label: "Consensus", Icon: Sparkles, threshold: 88 },
];

export function ProgressPanel({ progress }: { progress: ProgressState }) {
  return (
    <section className="glass overflow-hidden rounded-[28px] p-6 sm:p-8">
      <div className="flex items-start gap-4">
        <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl border border-indigo-300/20 bg-indigo-300/10 text-indigo-200">
          <LoaderCircle className="h-5 w-5 animate-spin" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-white">{progress.label}</p>
              <p className="mt-1 truncate text-sm text-slate-400">{progress.detail}</p>
            </div>
            <span className="text-sm font-semibold tabular-nums text-slate-300">{progress.percent}%</span>
          </div>
          <div className="relative mt-5 h-1.5 overflow-hidden rounded-full bg-white/[.06]">
            <div className="relative h-full rounded-full bg-gradient-to-r from-indigo-400 to-emerald-300 transition-all duration-500" style={{ width: `${progress.percent}%` }}>
              <div className="shimmer absolute inset-0 overflow-hidden rounded-full" />
            </div>
          </div>
        </div>
      </div>

      <div className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {steps.map(({ key, label, Icon, threshold }) => {
          const done = progress.percent > threshold + 15 || progress.percent === 100;
          const active = progress.stage.startsWith(key) || (key === "video" && progress.stage.startsWith("video_"));
          return (
            <div key={key} className={`rounded-2xl border px-3.5 py-3 transition ${active ? "border-indigo-300/25 bg-indigo-300/[.07]" : "border-white/[.06] bg-white/[.02]"}`}>
              <div className="mb-2 flex items-center justify-between">
                <Icon className={`h-4 w-4 ${active ? "text-indigo-200" : "text-slate-500"}`} />
                {done && <Check className="h-3.5 w-3.5 text-emerald-300" />}
              </div>
              <p className={`text-xs font-medium ${active ? "text-white" : "text-slate-500"}`}>{label}</p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
