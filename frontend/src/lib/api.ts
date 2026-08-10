import type { AnalysisResponse, AppConfig, ProgressState, ProviderChoice, VideoAnalysis } from "./types";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export async function getConfig(): Promise<AppConfig> {
  const response = await fetch(`${API}/api/config`, { cache: "no-store" });
  if (!response.ok) throw new Error("Could not reach the ReviewLens API.");
  return response.json();
}

interface StreamHandlers {
  onProgress: (progress: ProgressState) => void;
  onVideoResult?: (analysis: VideoAnalysis) => void;
}

export async function analyzeProduct(
  productName: string,
  analyzeComments: boolean,
  provider: ProviderChoice,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  const response = await fetch(`${API}/api/analyze/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_name: productName, analyze_comments: analyzeComments, provider }),
    signal,
  });
  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new Error(text || `Analysis request failed (${response.status}).`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: AnalysisResponse | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      if (!frame.trim()) continue;
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const parsed = JSON.parse(data);
      if (event === "progress") handlers.onProgress(parsed);
      else if (event === "video_result") handlers.onVideoResult?.(parsed.analysis);
      else if (event === "result") finalResult = parsed as AnalysisResponse;
      else if (event === "error") throw new Error(parsed.message || "Analysis failed.");
    }
  }

  if (!finalResult) throw new Error("The analysis stream ended before a final result was returned.");
  return finalResult;
}
