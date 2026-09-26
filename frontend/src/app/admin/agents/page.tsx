"use client";

import { useState } from "react";
import { Bot, Cpu } from "lucide-react";
import { AdminHeading } from "@/components/admin-ui";
import { ConfigurationList } from "@/components/admin-configurations";
import { AdminAgentModelsPanel } from "@/components/admin-agent-models";

export default function Page() {
  const [activeTab, setActiveTab] = useState<"models" | "versions">("models");

  return (
    <>
      <AdminHeading
        eyebrow="CONFIGURATION / AGENTS"
        title="Agent Control Plane"
        description="Configure which LLM model powers each agent in the analysis pipeline, or inspect versioned prompt definitions and golden evaluations."
      />

      <div
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 16,
          borderBottom: "1px solid var(--admin-border)",
          paddingBottom: 8,
        }}
      >
        <button
          type="button"
          onClick={() => setActiveTab("models")}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderRadius: 6,
            border: activeTab === "models" ? "1px solid var(--admin-primary)" : "1px solid transparent",
            background: activeTab === "models" ? "var(--admin-s2)" : "transparent",
            color: activeTab === "models" ? "var(--admin-text)" : "var(--admin-muted)",
            fontWeight: activeTab === "models" ? 650 : 500,
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          <Cpu size={16} /> Agent LLM Models
        </button>

        <button
          type="button"
          onClick={() => setActiveTab("versions")}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderRadius: 6,
            border: activeTab === "versions" ? "1px solid var(--admin-primary)" : "1px solid transparent",
            background: activeTab === "versions" ? "var(--admin-s2)" : "transparent",
            color: activeTab === "versions" ? "var(--admin-text)" : "var(--admin-muted)",
            fontWeight: activeTab === "versions" ? 650 : 500,
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          <Bot size={16} /> Advanced Definitions & Versions
        </button>
      </div>

      {activeTab === "models" ? (
        <AdminAgentModelsPanel />
      ) : (
        <ConfigurationList kind="agents" />
      )}
    </>
  );
}
