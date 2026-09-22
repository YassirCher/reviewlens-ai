"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Download,
  ExternalLink,
  FileText,
  Layers,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  User as UserIcon,
} from "lucide-react";
import { V2Shell } from "@/components/v2-shell";
import { useUserAuth } from "@/components/v2-auth-context";
import { adoptResearches, fetchUserResearches, type UserResearchItem } from "@/lib/user-auth";

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return "--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins === 0) return `${secs}s`;
  return `${mins}m ${secs}s`;
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(d);
  } catch {
    return iso;
  }
}

export default function ResearchesPage() {
  const { user, loading: authLoading, openAuthModal } = useUserAuth();
  const [researches, setResearches] = useState<UserResearchItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "complete" | "running" | "failed">("all");

  const loadResearches = async () => {
    setLoading(true);
    try {
      // First attempt to claim/adopt any anonymous session researches
      await adoptResearches();
      const items = await fetchUserResearches();
      setResearches(items);
    } catch {
      setResearches([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!user) return;
    let active = true;
    adoptResearches()
      .then(() => fetchUserResearches())
      .then((items) => {
        if (active) setResearches(items);
      })
      .catch(() => {
        if (active) setResearches([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user]);

  const filtered = useMemo(() => {
    return researches.filter((item) => {
      const matchesSearch =
        !search ||
        item.product_name.toLowerCase().includes(search.toLowerCase()) ||
        (item.verdict && item.verdict.toLowerCase().includes(search.toLowerCase()));

      let matchesStatus = true;
      if (statusFilter === "complete") {
        matchesStatus = item.status === "complete" || item.status === "partial";
      } else if (statusFilter === "running") {
        matchesStatus = item.status === "running" || item.status === "queued" || item.status === "cancelling";
      } else if (statusFilter === "failed") {
        matchesStatus = item.status === "failed" || item.status === "cancelled";
      }

      return matchesSearch && matchesStatus;
    });
  }, [researches, search, statusFilter]);

  const counts = useMemo(() => {
    return {
      all: researches.length,
      complete: researches.filter((r) => r.status === "complete" || r.status === "partial").length,
      running: researches.filter((r) => r.status === "running" || r.status === "queued" || r.status === "cancelling").length,
      failed: researches.filter((r) => r.status === "failed" || r.status === "cancelled").length,
    };
  }, [researches]);

  return (
    <V2Shell section="My Researches">
      <div className="v2-researches-page">
        {/* Top Header */}
        <div className="v2-researches-header">
          <div>
            <div className="v2-tag">
              <Sparkles size={13} aria-hidden="true" />
              <span>INTELLIGENCE ARCHIVE</span>
            </div>
            <h1>My Researches</h1>
            <p>Review and download all evidence-backed buying dossiers you have generated.</p>
          </div>
          <div className="v2-researches-actions">
            {user && (
              <button
                type="button"
                className="v2-button-secondary"
                onClick={loadResearches}
                title="Refresh history"
              >
                <RefreshCw size={15} aria-hidden="true" />
                Refresh
              </button>
            )}
            <Link href="/" className="v2-button-primary">
              <Plus size={16} aria-hidden="true" />
              New Research
            </Link>
          </div>
        </div>

        {/* Unauthenticated Gate */}
        {!authLoading && !user && (
          <div className="v2-auth-gate-card">
            <div className="v2-auth-gate-icon">
              <UserIcon size={32} aria-hidden="true" />
            </div>
            <h2>Sign in to see your researches</h2>
            <p>
              Your research runs, verified evidence, executive scorecards, and downloadable PDF dossiers are safely preserved in your account.
            </p>
            <div className="v2-auth-gate-buttons">
              <button
                type="button"
                className="v2-button-primary"
                onClick={() => openAuthModal("login")}
              >
                Sign In
              </button>
              <button
                type="button"
                className="v2-button-secondary"
                onClick={() => openAuthModal("register")}
              >
                Create Account
              </button>
            </div>
          </div>
        )}

        {/* Authenticated Content */}
        {user && (
          <>
            {/* Filter & Search Bar */}
            <div className="v2-researches-filters">
              <div className="v2-search-input-box">
                <Search size={16} className="v2-search-icon" aria-hidden="true" />
                <input
                  type="text"
                  placeholder="Search by product name or verdict..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
                {search && (
                  <button
                    type="button"
                    className="v2-clear-search"
                    onClick={() => setSearch("")}
                  >
                    Clear
                  </button>
                )}
              </div>

              <div className="v2-filter-chips" role="tablist">
                <button
                  type="button"
                  role="tab"
                  aria-selected={statusFilter === "all"}
                  className={`v2-chip ${statusFilter === "all" ? "active" : ""}`}
                  onClick={() => setStatusFilter("all")}
                >
                  All <span>({counts.all})</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={statusFilter === "complete"}
                  className={`v2-chip ${statusFilter === "complete" ? "active" : ""}`}
                  onClick={() => setStatusFilter("complete")}
                >
                  Completed <span>({counts.complete})</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={statusFilter === "running"}
                  className={`v2-chip ${statusFilter === "running" ? "active" : ""}`}
                  onClick={() => setStatusFilter("running")}
                >
                  In Progress <span>({counts.running})</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={statusFilter === "failed"}
                  className={`v2-chip ${statusFilter === "failed" ? "active" : ""}`}
                  onClick={() => setStatusFilter("failed")}
                >
                  Failed <span>({counts.failed})</span>
                </button>
              </div>
            </div>

            {/* List / Cards */}
            {loading ? (
              <div className="v2-researches-loading">
                <Loader2 size={28} className="v2-spin" aria-hidden="true" />
                <p>Retrieving your research dossiers...</p>
              </div>
            ) : filtered.length === 0 ? (
              <div className="v2-researches-empty">
                <div className="v2-empty-icon">
                  <FileText size={32} aria-hidden="true" />
                </div>
                <h3>{search || statusFilter !== "all" ? "No matching researches found" : "No researches yet"}</h3>
                <p>
                  {search || statusFilter !== "all"
                    ? "Try adjusting your search terms or filter to find what you are looking for."
                    : "Enter any product model on the home screen to synthesize top reviews and generate your first intelligence report."}
                </p>
                <Link href="/" className="v2-button-primary">
                  Start Your First Research <ArrowRight size={16} aria-hidden="true" />
                </Link>
              </div>
            ) : (
              <div className="v2-researches-grid">
                {filtered.map((item) => {
                  const isDone = item.status === "complete" || item.status === "partial";
                  const isRunning = item.status === "running" || item.status === "queued" || item.status === "cancelling";
                  const isFailed = item.status === "failed" || item.status === "cancelled";

                  return (
                    <article key={item.run_id} className="v2-research-card">
                      <div className="v2-card-top">
                        <div className="v2-card-title-group">
                          <h3>{item.product_name}</h3>
                          <span className="v2-card-date">{formatDate(item.created_at)}</span>
                        </div>
                        <span
                          className={`v2-status-pill ${
                            isDone ? "success" : isRunning ? "active" : "error"
                          }`}
                        >
                          {isDone && <CheckCircle2 size={13} aria-hidden="true" />}
                          {isRunning && <Loader2 size={13} className="v2-spin" aria-hidden="true" />}
                          {isFailed && <AlertTriangle size={13} aria-hidden="true" />}
                          <span>{item.status.toUpperCase()}</span>
                        </span>
                      </div>

                      {/* Score and Verdict if completed */}
                      {isDone && (
                        <div className="v2-card-verdict-row">
                          {item.overall_score !== null && (
                            <span className="v2-score-badge">
                              Score: <strong>{item.overall_score}</strong>/100
                            </span>
                          )}
                          {item.verdict && (
                            <span className="v2-verdict-tag">{item.verdict}</span>
                          )}
                        </div>
                      )}

                      {/* Summary snippet */}
                      {item.summary && (
                        <p className="v2-card-summary">
                          {item.summary.length > 180 ? item.summary.slice(0, 180) + "..." : item.summary}
                        </p>
                      )}

                      {/* Stats meta */}
                      <div className="v2-card-meta">
                        <div className="v2-meta-item">
                          <Layers size={14} aria-hidden="true" />
                          <span>
                            {item.source_count_analyzed} of {item.source_count_requested} sources
                          </span>
                        </div>
                        {item.duration_seconds !== null && item.duration_seconds > 0 && (
                          <div className="v2-meta-item">
                            <Clock size={14} aria-hidden="true" />
                            <span>{formatDuration(item.duration_seconds)}</span>
                          </div>
                        )}
                      </div>

                      {/* Card Action Buttons */}
                      <div className="v2-card-actions">
                        {isDone && item.public_token && (
                          <>
                            <Link
                              href={`/r/${item.public_token}`}
                              className="v2-button-primary v2-card-btn"
                            >
                              <span>View Report</span>
                              <ExternalLink size={14} aria-hidden="true" />
                            </Link>
                            <a
                              href={`${process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"}/api/v2/reports/${item.public_token}/pdf`}
                              download={`ReviewLens-${item.product_name.replace(/[^a-zA-Z0-9_-]+/g, "-")}-Dossier.pdf`}
                              className="v2-button-secondary v2-card-btn"
                              title="Download ReportLab PDF Dossier"
                            >
                              <Download size={14} aria-hidden="true" />
                              <span>PDF</span>
                            </a>
                          </>
                        )}

                        {isRunning && (
                          <Link
                            href={`/analysis/${item.run_id}`}
                            className="v2-button-secondary v2-card-btn"
                          >
                            <span>Track Live Progress</span>
                            <ArrowRight size={14} aria-hidden="true" />
                          </Link>
                        )}

                        {isFailed && (
                          <Link
                            href={`/analysis/${item.run_id}`}
                            className="v2-button-secondary v2-card-btn"
                          >
                            <span>Inspect Error Details</span>
                            <ArrowRight size={14} aria-hidden="true" />
                          </Link>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </V2Shell>
  );
}
