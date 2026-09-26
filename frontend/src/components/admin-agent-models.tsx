"use client";

import { useEffect, useState } from "react";
import { Bot, CheckCircle2, Cpu, RotateCcw, Save, Sparkles } from "lucide-react";
import { adminPut } from "@/lib/admin";
import { AdminState, useAdminData } from "@/components/admin-ui";

export type AgentModelInfo = {
  key: string;
  name: string;
  purpose: string;
  current_model: string;
  is_default: boolean;
  default_model: string;
  max_input_tokens: number;
  max_output_tokens: number;
  max_total_tokens: number;
  timeout_seconds: number;
};

export type AvailableModel = {
  slug: string;
  name: string;
  author: string;
  context_length: number | null;
  is_default: boolean;
};

export type AgentModelsState = {
  default_model: string;
  agents: AgentModelInfo[];
  available_models: AvailableModel[];
  active_workflow_version_id: string | null;
};

export function AdminAgentModelsPanel() {
  const { data, loading, error, reload } = useAdminData<AgentModelsState>("/agent-models");
  const [selectedModels, setSelectedModels] = useState<Record<string, string>>({});
  const [customInputMode, setCustomInputMode] = useState<Record<string, boolean>>({});
  const [message, setMessage] = useState("");
  const [isSuccess, setIsSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  // Initialize local selection state from fetched data
  useEffect(() => {
    if (!data?.agents) return;
    const initial: Record<string, string> = {};
    for (const a of data.agents) {
      initial[a.key] = a.current_model;
    }
    setSelectedModels(initial);
  }, [data]);

  const defaultModel = data?.default_model || "deepseek/deepseek-v4-flash";
  const availableModels = data?.available_models || [];

  const hasUnsavedChanges = data?.agents
    ? data.agents.some((a) => (selectedModels[a.key] || defaultModel) !== a.current_model)
    : false;

  const customizedCount = data?.agents
    ? data.agents.filter((a) => (selectedModels[a.key] || defaultModel) !== defaultModel).length
    : 0;

  function handleSetDefault(key: string) {
    setSelectedModels((prev) => ({ ...prev, [key]: defaultModel }));
    setCustomInputMode((prev) => ({ ...prev, [key]: false }));
  }

  function handleSetCustom(key: string, modelSlug: string) {
    setSelectedModels((prev) => ({ ...prev, [key]: modelSlug }));
  }

  function handleResetAll() {
    if (!data?.agents) return;
    const reset: Record<string, string> = {};
    const inputModes: Record<string, boolean> = {};
    for (const a of data.agents) {
      reset[a.key] = defaultModel;
      inputModes[a.key] = false;
    }
    setSelectedModels(reset);
    setCustomInputMode(inputModes);
  }

  async function handleSave() {
    setBusy(true);
    setMessage("");
    setIsSuccess(false);
    try {
      await adminPut("/agent-models", { models: selectedModels });
      setIsSuccess(true);
      setMessage("Agent model assignments saved and active workflow updated.");
      await reload();
    } catch (err) {
      setIsSuccess(false);
      setMessage(err instanceof Error ? err.message : "Failed to save agent model assignments.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-panel" style={{ marginTop: 16 }}>
      <div className="admin-panel-head" style={{ alignItems: "flex-start", gap: 16 }}>
        <div>
          <h2 style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Cpu size={20} /> Agent LLM Model Configuration
          </h2>
          <p className="admin-muted" style={{ marginTop: 4 }}>
            Control which OpenRouter model powers each of the 8 analysis agents. By default, every agent runs with{" "}
            <strong className="admin-mono" style={{ color: "var(--admin-primary)" }}>
              {defaultModel}
            </strong>
            .
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className="admin-secondary"
            onClick={handleResetAll}
            disabled={busy || !data}
            title="Reset all agents to global default model"
          >
            <RotateCcw size={15} /> Reset All to Default
          </button>
          <button
            type="button"
            className="admin-primary"
            onClick={handleSave}
            disabled={busy || !hasUnsavedChanges}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <Save size={15} />
            {busy ? "Saving…" : "Save & Apply Models"}
            {hasUnsavedChanges && (
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  backgroundColor: "var(--admin-amber)",
                  display: "inline-block",
                }}
              />
            )}
          </button>
        </div>
      </div>

      {message && (
        <div
          className={`admin-banner ${isSuccess ? "admin-banner-success" : "admin-banner-error"}`}
          role="status"
          style={{
            marginTop: 12,
            display: "flex",
            alignItems: "center",
            gap: 8,
            backgroundColor: isSuccess ? "rgba(53, 208, 186, 0.15)" : undefined,
            borderColor: isSuccess ? "var(--admin-teal)" : undefined,
            color: isSuccess ? "var(--admin-teal)" : undefined,
          }}
        >
          {isSuccess ? <CheckCircle2 size={16} /> : null}
          {message}
        </div>
      )}

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          margin: "16px 0 20px",
          padding: "10px 14px",
          background: "var(--admin-s2)",
          borderRadius: 8,
          border: "1px solid var(--admin-border)",
          fontSize: 13,
        }}
      >
        <span className="admin-muted">
          Active configuration status:{" "}
          <strong style={{ color: customizedCount > 0 ? "var(--admin-primary)" : "var(--admin-teal)" }}>
            {customizedCount === 0 ? "All 8 agents using default model" : `${customizedCount} of 8 agents customized`}
          </strong>
        </span>
        {hasUnsavedChanges && (
          <span style={{ color: "var(--admin-amber)", fontWeight: 600 }}>Unsaved changes</span>
        )}
      </div>

      <AdminState loading={loading} error={error} empty={!data?.agents?.length}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 14 }}>
          {data?.agents.map((agent) => {
            const currentSelected = selectedModels[agent.key] || defaultModel;
            const isUsingDefault = currentSelected === defaultModel;
            const isCustomInput = customInputMode[agent.key];

            return (
              <article
                key={agent.key}
                style={{
                  background: "var(--admin-s1)",
                  border: isUsingDefault
                    ? "1px solid var(--admin-border)"
                    : "1px solid var(--admin-primary)",
                  borderRadius: 10,
                  padding: 16,
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  boxShadow: !isUsingDefault ? "0 0 12px rgba(139, 124, 246, 0.15)" : undefined,
                  transition: "all 0.2s ease",
                }}
              >
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        style={{
                          width: 28,
                          height: 28,
                          borderRadius: 6,
                          background: isUsingDefault ? "var(--admin-s2)" : "rgba(139, 124, 246, 0.2)",
                          color: isUsingDefault ? "var(--admin-muted)" : "var(--admin-primary)",
                          display: "grid",
                          placeItems: "center",
                        }}
                      >
                        <Bot size={16} />
                      </span>
                      <div>
                        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 650 }}>{agent.name}</h3>
                        <span className="admin-mono admin-muted" style={{ fontSize: 11 }}>
                          {agent.key}
                        </span>
                      </div>
                    </div>
                    <span
                      style={{
                        fontSize: 11,
                        padding: "2px 8px",
                        borderRadius: 12,
                        background: isUsingDefault ? "var(--admin-s3)" : "var(--admin-primary)",
                        color: isUsingDefault ? "var(--admin-muted)" : "var(--admin-primary-text)",
                        fontWeight: 600,
                      }}
                    >
                      {isUsingDefault ? "Default" : "Custom"}
                    </span>
                  </div>

                  <p
                    className="admin-muted"
                    style={{ fontSize: 12, marginTop: 10, marginBottom: 14, minHeight: 34, lineHeight: 1.4 }}
                  >
                    {agent.purpose}
                  </p>
                </div>

                <div style={{ borderTop: "1px solid var(--admin-border)", paddingTop: 12 }}>
                  <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
                    <button
                      type="button"
                      onClick={() => handleSetDefault(agent.key)}
                      style={{
                        flex: 1,
                        fontSize: 12,
                        padding: "6px 10px",
                        borderRadius: 6,
                        border: isUsingDefault ? "1px solid var(--admin-teal)" : "1px solid var(--admin-border)",
                        background: isUsingDefault ? "rgba(53, 208, 186, 0.12)" : "transparent",
                        color: isUsingDefault ? "var(--admin-teal)" : "var(--admin-muted)",
                        fontWeight: isUsingDefault ? 650 : 500,
                        cursor: "pointer",
                      }}
                    >
                      Default LLM
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (isUsingDefault) {
                          // pick the first non-default recommended model as starting suggestion
                          const firstCustom = availableModels.find((m) => !m.is_default)?.slug || "deepseek/deepseek-v4-flash-0731";
                          handleSetCustom(agent.key, firstCustom);
                        }
                      }}
                      style={{
                        flex: 1,
                        fontSize: 12,
                        padding: "6px 10px",
                        borderRadius: 6,
                        border: !isUsingDefault ? "1px solid var(--admin-primary)" : "1px solid var(--admin-border)",
                        background: !isUsingDefault ? "rgba(139, 124, 246, 0.15)" : "transparent",
                        color: !isUsingDefault ? "var(--admin-primary)" : "var(--admin-muted)",
                        fontWeight: !isUsingDefault ? 650 : 500,
                        cursor: "pointer",
                      }}
                    >
                      <Sparkles size={12} style={{ display: "inline", marginRight: 4 }} />
                      Custom LLM
                    </button>
                  </div>

                  {isUsingDefault ? (
                    <div
                      style={{
                        padding: "8px 10px",
                        background: "var(--admin-s2)",
                        borderRadius: 6,
                        fontSize: 12,
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <span className="admin-mono" style={{ color: "var(--admin-text)" }}>
                        {defaultModel}
                      </span>
                      <span className="admin-muted" style={{ fontSize: 11 }}>
                        Global default
                      </span>
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {!isCustomInput ? (
                        <select
                          value={currentSelected}
                          onChange={(e) => {
                            if (e.target.value === "__custom__") {
                              setCustomInputMode((prev) => ({ ...prev, [agent.key]: true }));
                            } else {
                              handleSetCustom(agent.key, e.target.value);
                            }
                          }}
                          style={{
                            width: "100%",
                            padding: "8px 10px",
                            background: "var(--admin-s2)",
                            color: "var(--admin-text)",
                            border: "1px solid var(--admin-border-strong)",
                            borderRadius: 6,
                            fontSize: 12,
                            fontFamily: "var(--v2-mono, monospace)",
                          }}
                        >
                          <optgroup label="Recommended Models">
                            {availableModels.map((m) => (
                              <option key={m.slug} value={m.slug}>
                                {m.name} ({m.slug})
                              </option>
                            ))}
                          </optgroup>
                          <option value="__custom__">+ Enter custom OpenRouter slug…</option>
                        </select>
                      ) : (
                        <div style={{ display: "flex", gap: 6 }}>
                          <input
                            type="text"
                            value={currentSelected}
                            placeholder="e.g. anthropic/claude-3.5-sonnet"
                            onChange={(e) => handleSetCustom(agent.key, e.target.value)}
                            style={{
                              flex: 1,
                              padding: "6px 8px",
                              background: "var(--admin-s2)",
                              color: "var(--admin-text)",
                              border: "1px solid var(--admin-border-strong)",
                              borderRadius: 6,
                              fontSize: 12,
                              fontFamily: "var(--v2-mono, monospace)",
                            }}
                          />
                          <button
                            type="button"
                            className="admin-secondary"
                            onClick={() => setCustomInputMode((prev) => ({ ...prev, [agent.key]: false }))}
                            style={{ fontSize: 11, padding: "4px 8px" }}
                          >
                            Presets
                          </button>
                        </div>
                      )}
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
                        <span className="admin-muted">Model slug:</span>
                        <span className="admin-mono" style={{ color: "var(--admin-primary)" }}>
                          {currentSelected}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </AdminState>
    </section>
  );
}
