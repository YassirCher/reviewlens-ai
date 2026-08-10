export type Verdict = "buy" | "buy_with_caveats" | "mixed" | "do_not_buy" | "unclear";
export type ProviderChoice = "auto" | "openrouter" | "xai" | "openai";

export interface ProviderInfo {
  id: string;
  label: string;
  available: boolean;
  model: string;
  free_friendly: boolean;
}

export interface AppConfig {
  providers: ProviderInfo[];
  default_provider: string;
  comments_default: boolean;
  max_videos: number;
}

export interface EvidenceItem {
  claim: string;
  evidence_text: string;
  timestamp_seconds: number | null;
  confidence: number;
}

export interface CommentAnalysis {
  comments_analyzed: number;
  positive_pct: number;
  neutral_pct: number;
  negative_pct: number;
  recurring_pros: string[];
  recurring_cons: string[];
  repeated_issues: string[];
  audience_agrees_with_reviewer: boolean | null;
  confidence_score: number;
}

export interface VideoAnalysis {
  video: {
    video_id: string;
    title: string;
    channel: string;
    url: string;
    thumbnail_url?: string | null;
    view_count: number;
    duration_seconds?: number | null;
    published_at?: string | null;
    relevance_score: number;
  };
  review_type: string;
  usage_period_mentioned: boolean;
  usage_period_raw: string | null;
  usage_period_days_estimate: number | null;
  ownership_context: string;
  product_score: number;
  reviewer_sentiment_score: number;
  purchase_recommendation_score: number;
  confidence_score: number;
  purchase_verdict: Verdict;
  recommendation_summary: string;
  pros: string[];
  cons: string[];
  major_issues: string[];
  recommended_for: string[];
  not_recommended_for: string[];
  evidence: EvidenceItem[];
  comments?: CommentAnalysis | null;
}

export interface AnalysisResponse {
  analysis_id: string;
  product_name: string;
  analyze_comments: boolean;
  provider_used: string;
  model_used: string;
  videos: VideoAnalysis[];
  overall: {
    score: number;
    verdict: Verdict;
    confidence: number;
    summary: string;
    consensus_pros: string[];
    consensus_cons: string[];
    disagreements: string[];
    longest_usage_period: string | null;
    who_should_buy: string[];
    who_should_avoid: string[];
  } | null;
  warnings: string[];
}

export interface ProgressState {
  stage: string;
  label: string;
  percent: number;
  detail: string;
}
