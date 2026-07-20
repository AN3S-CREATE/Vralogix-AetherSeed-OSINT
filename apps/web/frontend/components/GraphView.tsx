"use client";

import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import { useMemo } from "react";
import type { NodeLinkGraph } from "@/lib/api";
import { toReactFlow } from "@/lib/graph";

export function GraphView({ graph }: { graph: NodeLinkGraph | undefined }) {
  const { nodes, edges } = useMemo(
    () => (graph ? toReactFlow(graph) : { nodes: [], edges: [] }),
    [graph],
  );

  if (!graph || nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-400">
        No graph yet — run an investigation.
      </div>
    );
  }

  return (
    <ReactFlow nodes={nodes} edges={edges} fitView minZoom={0.1} proOptions={{ hideAttribution: true }}>
      <Background color="#1e293b" gap={20} />
      <MiniMap pannable zoomable className="!bg-panel" />
      <Controls className="!bg-panel" />
    </ReactFlow>
  );
}
