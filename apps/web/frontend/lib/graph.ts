// Transform the API's node-link graph into React Flow nodes/edges with a
// simple deterministic circular layout and type-based colouring.

import type { Edge, Node } from "@xyflow/react";
import type { NodeLinkGraph } from "./api";

const TYPE_COLORS: Record<string, string> = {
  person: "#60a5fa",
  company: "#34d399",
  domain: "#f59e0b",
  asset: "#a78bfa",
  transaction: "#f472b6",
  location: "#22d3ee",
  document: "#94a3b8",
  account: "#fbbf24",
  event: "#fb7185",
  other: "#cbd5e1",
};

export function toReactFlow(graph: NodeLinkGraph): { nodes: Node[]; edges: Edge[] } {
  const n = Math.max(graph.nodes.length, 1);
  const radius = 120 + n * 22;

  const nodes: Node[] = graph.nodes.map((node, i) => {
    const angle = (2 * Math.PI * i) / n;
    const color = TYPE_COLORS[node.type ?? "other"] ?? TYPE_COLORS.other;
    return {
      id: node.id,
      position: { x: radius * Math.cos(angle) + radius, y: radius * Math.sin(angle) + radius },
      data: { label: `${node.label ?? node.id}\n(${node.type ?? "?"})` },
      style: {
        background: color,
        color: "#0b1020",
        border: "1px solid rgba(0,0,0,0.25)",
        borderRadius: 8,
        fontSize: 11,
        padding: 6,
        width: 150,
        whiteSpace: "pre-wrap",
      },
    };
  });

  const edges: Edge[] = graph.links.map((link, i) => ({
    id: link.key ?? `e-${link.source}-${link.target}-${i}`,
    source: link.source,
    target: link.target,
    label: link.type ?? "",
    animated: link.type === "paid" || link.type === "controls",
    style: { stroke: "#64748b" },
    labelStyle: { fill: "#cbd5e1", fontSize: 10 },
  }));

  return { nodes, edges };
}
