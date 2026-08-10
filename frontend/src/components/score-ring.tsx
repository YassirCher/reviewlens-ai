import type { CSSProperties } from "react";

function scoreColor(score: number) {
  if (score >= 80) return "#6de6c3";
  if (score >= 65) return "#8b9cff";
  if (score >= 45) return "#f2ca72";
  return "#ff7b91";
}

export function ScoreRing({ score, size = "lg" }: { score: number; size?: "sm" | "lg" }) {
  const outer = size === "lg" ? "h-36 w-36" : "h-20 w-20";
  const inner = size === "lg" ? "inset-[7px]" : "inset-[5px]";
  const text = size === "lg" ? "text-4xl" : "text-xl";
  return (
    <div
      className={`score-ring relative shrink-0 rounded-full ${outer}`}
      style={{ "--score": score, "--ring-color": scoreColor(score) } as CSSProperties}
      aria-label={`Score ${score} out of 100`}
    >
      <div className={`absolute ${inner} grid place-items-center rounded-full border border-white/[.06] bg-[#0b0f17] shadow-inner`}>
        <div className="text-center">
          <div className={`${text} font-semibold tracking-[-0.05em] text-white`}>{score}</div>
          <div className="mt-0.5 text-[9px] font-bold uppercase tracking-[.2em] text-slate-500">/ 100</div>
        </div>
      </div>
    </div>
  );
}
