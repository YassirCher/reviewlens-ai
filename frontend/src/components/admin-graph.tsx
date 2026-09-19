"use client";

import { useMemo } from "react";
import { ReactFlow, Background, Controls, MiniMap, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

type GraphItem = { id: string; label: string; kind: string };
type GraphLink = { source: string; target: string; label: string };

export function AdminGraph({ items, links, title, onSelect }: { items: GraphItem[]; links: GraphLink[]; title: string; onSelect?: (id: string) => void }) {
  const graph = useMemo(() => {
    const nodes: Node[] = items.slice(0, 100).map((item, index) => ({ id: item.id,
      position: { x: (index % 5) * 230, y: Math.floor(index / 5) * 115 },
      data: { label: <div className="admin-graph-node"><small>{item.kind}</small><strong>{item.label}</strong></div> },
      style: { background: "transparent", border: 0, padding: 0 },
    }));
    const ids = new Set(nodes.map(node => node.id));
    const edges: Edge[] = links.filter(item => ids.has(item.source) && ids.has(item.target)).slice(0, 200)
      .map((item, index) => ({ id: `edge-${index}`, source: item.source, target: item.target,
        label: item.label, style: { stroke: "#64748b" }, labelStyle: { fill: "#aab4c3" } }));
    return { nodes, edges };
  }, [items, links]);
  return <div><div className="admin-graph" role="region" aria-label={title + " canvas"}><ReactFlow nodes={graph.nodes} edges={graph.edges} fitView nodesDraggable={false} nodesConnectable={false} nodesFocusable={false} edgesFocusable={false} elementsSelectable={false} zoomOnDoubleClick={false} minZoom={0.25} maxZoom={1.5}><Background color="#2a3342" /><MiniMap pannable /><Controls showInteractive={false} /></ReactFlow></div><details className="admin-graph-alt"><summary>Readable {title} list ({items.length} nodes, {links.length} relations)</summary><ul className="admin-list">{items.map(item => <li key={item.id}><div><strong>{item.label}</strong><br /><span className="admin-muted">{item.kind} · {item.id}</span></div>{onSelect && <button className="admin-secondary" onClick={() => onSelect(item.id)}>Inspect</button>}</li>)}</ul><h3>Relations</h3><ul>{links.map((link, index) => <li key={index}>{link.source} → {link.target} ({link.label})</li>)}</ul></details></div>;
}

export function WorkflowGraph({ dag }: { dag: Record<string, unknown> }) {
  const specs = ((dag.templates || dag.tasks || []) as Record<string, unknown>[]);
  const items = specs.map((task, index) => ({ id: String(task.template_key || task.task_key || index),
    label: String(task.task_key || task.template_key || index), kind: String(task.handler || "task") }));
  const links = specs.flatMap(task => ((task.dependencies || []) as string[]).map(from => ({
    source: from, target: String(task.template_key || task.task_key), label: "depends on" })));
  return <AdminGraph items={items} links={links} title="workflow" />;
}
