import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { getGraph } from "../api/client";
import { groupAnchors } from "../graph/layout";
import { useBrain } from "../store";
import type { GraphData, GraphNode } from "../types";

/**
 * The brain graph — every /api/graph node, type-styled, pinned to deterministic
 * group geography (graph/layout.ts). Canvas-2D only (no WebGL — the GPU belongs
 * to the LLM). B2 is static: no edge particles / live gauge yet (that's B3).
 */

const RADIUS: Record<string, number> = {
  core: 16,
  brain: 12,
  safety: 11,
  router: 10,
  memory: 9,
  voice: 9,
  infra: 8,
  tool: 6,
};

const TYPE_VAR: Record<string, string> = {
  core: "--node-core",
  router: "--node-router",
  safety: "--node-safety",
  memory: "--node-memory",
  voice: "--node-voice",
  infra: "--node-infra",
  brain: "--node-brain",
  tool: "--node-tool",
};

// Resolve design-token colors once from tokens.css — single source of truth.
const _colorCache: Record<string, string> = {};
function colorFor(type: string): string {
  if (_colorCache[type]) return _colorCache[type];
  const varName = TYPE_VAR[type] ?? "--node-infra";
  const v =
    getComputedStyle(document.documentElement).getPropertyValue(varName).trim() ||
    "#9aa7bd";
  _colorCache[type] = v;
  return v;
}

interface Size {
  w: number;
  h: number;
}

function useSize(): [React.RefObject<HTMLDivElement>, Size] {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<Size>({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

export default function BrainGraph() {
  const [wrapRef, size] = useSize();
  const fgRef = useRef<any>(null);
  const [raw, setRaw] = useState<GraphData | null>(null);

  const selected = useBrain((s) => s.selectedNode);
  const selectedRef = useRef<string | null>(selected);
  selectedRef.current = selected;

  useEffect(() => {
    getGraph()
      .then(setRaw)
      .catch(() => setRaw({ nodes: [], edges: [] }));
  }, []);

  // Map to react-force-graph shape with pinned fx/fy. Recomputed only when the
  // topology changes (rare) — node object identity stays stable across renders.
  const data = useMemo(() => {
    if (!raw) return { nodes: [], links: [] };
    const anchors = groupAnchors(raw.nodes);
    const nodes = raw.nodes.map((n: GraphNode) => {
      const a = anchors.get(n.id);
      return { ...n, fx: a?.fx, fy: a?.fy };
    });
    const links = raw.edges.map((e) => ({ source: e.source, target: e.target }));
    return { nodes, links };
  }, [raw]);

  const edgeColor = useMemo(
    () =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--edge")
        .trim() || "#2a2f3a",
    [],
  );

  // Re-fit the camera whenever the container resizes (panel collapse / window
  // resize) or the topology loads — the engine only auto-fits once on stop.
  useEffect(() => {
    if (!data.nodes.length || size.w <= 0 || size.h <= 0) return;
    const id = setTimeout(() => fgRef.current?.zoomToFit(300, 50), 60);
    return () => clearTimeout(id);
  }, [size.w, size.h, data]);

  const drawNode = (node: any, ctx: CanvasRenderingContext2D, scale: number) => {
    const type = String(node.type);
    const r = RADIUS[type] ?? 7;
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI, false);
    ctx.fillStyle = colorFor(type);
    ctx.fill();

    if (node.id === selectedRef.current) {
      ctx.lineWidth = 2 / scale;
      ctx.strokeStyle = "#e6e9ef";
      ctx.beginPath();
      ctx.arc(node.x, node.y, r + 4 / scale, 0, 2 * Math.PI, false);
      ctx.stroke();
    }

    const big =
      type === "core" || type === "brain" || type === "router" || type === "safety";
    if (big || scale > 1.6) {
      const fontSize = Math.max(11 / scale, 2.5);
      ctx.font = `${fontSize}px "Segoe UI", system-ui, sans-serif`;
      ctx.fillStyle = "#e6e9ef";
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(String(node.label), node.x, node.y + r + 2 / scale);
    }
  };

  const paintPointer = (node: any, color: string, ctx: CanvasRenderingContext2D) => {
    const r = (RADIUS[String(node.type)] ?? 7) + 3;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI, false);
    ctx.fill();
  };

  return (
    <div className="graph-wrap" ref={wrapRef}>
      {size.w > 0 && size.h > 0 && (
        <ForceGraph2D
          ref={fgRef}
          width={size.w}
          height={size.h}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          cooldownTicks={0}
          warmupTicks={80}
          enableNodeDrag={false}
          nodeCanvasObject={drawNode}
          nodePointerAreaPaint={paintPointer}
          nodeLabel={(n: any) => `${n.label} — ${n.role || n.type}`}
          linkColor={() => edgeColor}
          linkWidth={1}
          onNodeClick={(n: any) => useBrain.getState().selectNode(String(n.id))}
          onBackgroundClick={() => useBrain.getState().selectNode(null)}
          onEngineStop={() => fgRef.current?.zoomToFit(400, 50)}
        />
      )}
    </div>
  );
}
