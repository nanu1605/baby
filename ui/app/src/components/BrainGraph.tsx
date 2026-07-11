import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { getGraph } from "../api/client";
import { groupAnchors } from "../graph/layout";
import { subscribePulses } from "../graph/pulseBus";
import type { PulseClass } from "../graph/pulseBus";
import { startRenderClock } from "../graph/renderClock";
import { useReducedMotion } from "../hooks/useReducedMotion";
import { useBrain } from "../store";
import type { GraphData, GraphNode, PipelineState } from "../types";

/**
 * The living brain graph (B3). B2's static topology + honest animation: edge
 * particles fired from real events (via pulseBus), the central Baby-core gauge
 * driven by /ws/state, live node recolor (router health / active brain / game
 * ghost), and an idle-throttled render clock so the canvas is cheap when quiet.
 * Canvas-2D only — the GPU belongs to the LLM.
 */

const RADIUS: Record<string, number> = {
  core: 16, brain: 12, safety: 11, router: 10, memory: 9, voice: 9, infra: 8, tool: 6,
};

const TYPE_VAR: Record<string, string> = {
  core: "--node-core", router: "--node-router", safety: "--node-safety",
  memory: "--node-memory", voice: "--node-voice", infra: "--node-infra",
  brain: "--node-brain", tool: "--node-tool",
};

const STATE_VAR: Record<PipelineState, string> = {
  idle: "--state-idle", listening: "--state-listening", thinking: "--state-thinking",
  speaking: "--state-speaking", executing: "--state-executing",
};

const PULSE_VAR: Record<PulseClass, string> = {
  normal: "--pulse-normal", confirm: "--pulse-confirm", error: "--pulse-error",
};

const _cssCache: Record<string, string> = {};
function cssVar(name: string): string {
  if (_cssCache[name]) return _cssCache[name];
  const v =
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#9aa7bd";
  _cssCache[name] = v;
  return v;
}
const now = () => performance.now();

interface Size { w: number; h: number; }

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

  // Store slices (low-freq) — read live in the draw closure via refs.
  const pipeline = useBrain((s) => s.pipeline);
  const router = useBrain((s) => s.router);
  const gameMode = useBrain((s) => s.gameMode);
  const activeBrain = useBrain((s) => s.activeBrain);
  const performanceMode = useBrain((s) => s.performanceMode);
  const selected = useBrain((s) => s.selectedNode);

  const pipelineRef = useRef(pipeline); pipelineRef.current = pipeline;
  const routerRef = useRef(router); routerRef.current = router;
  const gameRef = useRef(gameMode); gameRef.current = gameMode;
  const activeRef = useRef(activeBrain); activeRef.current = activeBrain;
  const perfRef = useRef(performanceMode); perfRef.current = performanceMode;
  const selRef = useRef(selected); selRef.current = selected;

  // Shared reduced-motion source (#116); mirrored into a ref for the draw closure.
  const reduced = useReducedMotion();
  const reducedRef = useRef(reduced); reducedRef.current = reduced;

  // pulse/flash runtime state
  const linkMap = useRef<Map<string, any>>(new Map());
  const lastEmit = useRef<Map<string, number>>(new Map());
  const flashes = useRef<Map<string, { klass: PulseClass; expiry: number }>>(new Map());
  const lastActivity = useRef(0);
  const bump = () => { lastActivity.current = now(); };

  const isLowPower = () => perfRef.current || reducedRef.current;

  useEffect(() => {
    getGraph()
      .then((g) => {
        setRaw(g);
        useBrain.getState().setGraph(g); // lift topology for the inspector drawer
      })
      .catch(() => setRaw({ nodes: [], edges: [] }));
  }, []);

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

  // Build the from>to → link-object identity map (emitParticle needs the object).
  useEffect(() => {
    const m = new Map<string, any>();
    for (const l of data.links as any[]) {
      const s = typeof l.source === "object" ? l.source.id : l.source;
      const t = typeof l.target === "object" ? l.target.id : l.target;
      m.set(`${s}>${t}`, l);
    }
    linkMap.current = m;
  }, [data]);

  // Subscribe to honest pulses/flashes; coalesce particles per edge.
  useEffect(() => {
    return subscribePulses((a) => {
      if (a.type === "flash") {
        flashes.current.set(a.node, { klass: a.klass, expiry: now() + 600 });
        bump();
        return;
      }
      if (isLowPower()) return; // no particles in low-power
      const key = `${a.from}>${a.to}`;
      const t = now();
      if (t - (lastEmit.current.get(key) ?? 0) < 150) return; // coalesce (~≤6/s/edge)
      const link = linkMap.current.get(key);
      if (!link) return;
      link.__pulseKlass = a.klass;
      fgRef.current?.emitParticle(link);
      lastEmit.current.set(key, t);
      bump();
    });
  }, []);

  const edgeColor = useMemo(() => cssVar("--edge"), []);

  // Refit on container resize / topology load (engine only auto-fits once).
  useEffect(() => {
    if (!data.nodes.length || size.w <= 0 || size.h <= 0) return;
    const id = setTimeout(() => { fgRef.current?.zoomToFit(300, 50); bump(); }, 60);
    return () => clearTimeout(id);
  }, [size.w, size.h, data]);

  // Fly the camera to the selected node (graph click, drawer, deep-link, search).
  useEffect(() => {
    if (!selected) return;
    const n = (data.nodes as { id: string; x?: number; y?: number }[]).find(
      (x) => x.id === selected,
    );
    if (!n || typeof n.x !== "number" || typeof n.y !== "number") return;
    const fg = fgRef.current;
    if (!fg) return;
    fg.centerAt(n.x, n.y, 600);
    if ((fg.zoom?.() ?? 1) < 2) fg.zoom(2.2, 600);
    bump();
  }, [selected]);

  // The self-owned render clock: 60fps active / ~22fps idle / off in low-power-idle.
  const clockStarted = useRef(false);
  const startClock = () => {
    if (clockStarted.current) return;
    clockStarted.current = true;
    const stop = startRenderClock({
      getActive: () => pipelineRef.current !== "idle" || now() - lastActivity.current < 1500,
      isLowPower,
      draw: () => { const fg = fgRef.current; if (fg) { fg.resumeAnimation(); fg.pauseAnimation(); } },
    });
    clockStop.current = stop;
  };
  const clockStop = useRef<null | (() => void)>(null);
  useEffect(() => () => clockStop.current?.(), []);

  // --- drawing ---------------------------------------------------------------
  const drawGauge = (node: any, ctx: CanvasRenderingContext2D, scale: number) => {
    const t = now();
    const p = pipelineRef.current;
    const rm = reducedRef.current || perfRef.current;
    const base = RADIUS.core;
    const cx = node.x, cy = node.y;
    const breath = rm ? 1 : 1 + 0.06 * Math.sin(t / 700);
    const r = base * (p === "idle" ? breath : 1);

    ctx.fillStyle = cssVar("--node-core");
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, 2 * Math.PI);
    ctx.fill();

    const col = cssVar(STATE_VAR[p]);
    ctx.strokeStyle = col;
    ctx.lineWidth = 2 / scale;
    if (rm) {
      // static state ring in low-power / reduced-motion
      ctx.globalAlpha = 0.8;
      ctx.beginPath(); ctx.arc(cx, cy, base + 4, 0, 2 * Math.PI); ctx.stroke();
      ctx.globalAlpha = 1;
    } else if (p === "listening") {
      const rr = base + 6 + 4 * Math.sin(t / 400);
      ctx.beginPath(); ctx.arc(cx, cy, rr, 0, 2 * Math.PI); ctx.stroke();
    } else if (p === "thinking") {
      const orbit = base + 8;
      for (let i = 0; i < 3; i++) {
        const a = t / 500 + (i * 2 * Math.PI) / 3;
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(a) * orbit, cy + Math.sin(a) * orbit, 2, 0, 2 * Math.PI);
        ctx.fill();
      }
    } else if (p === "speaking") {
      ctx.globalAlpha = 0.35 + 0.35 * Math.sin(t / 300);
      ctx.beginPath(); ctx.arc(cx, cy, base + 5, 0, 2 * Math.PI); ctx.stroke();
      ctx.globalAlpha = 1;
    } else if (p === "executing") {
      const a = t / 300;
      ctx.beginPath(); ctx.arc(cx, cy, base + 5, a, a + Math.PI * 1.3); ctx.stroke();
    } else {
      // idle breathing halo
      ctx.globalAlpha = 0.5;
      ctx.beginPath(); ctx.arc(cx, cy, r + 3, 0, 2 * Math.PI); ctx.stroke();
      ctx.globalAlpha = 1;
    }

    label(ctx, node.label, cx, cy + base + 3 / scale, scale);
  };

  const label = (ctx: CanvasRenderingContext2D, text: string, x: number, y: number, scale: number) => {
    const fontSize = Math.max(11 / scale, 2.5);
    ctx.font = `${fontSize}px "Segoe UI", system-ui, sans-serif`;
    ctx.fillStyle = "#e6e9ef";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(text, x, y);
  };

  const drawNode = (node: any, ctx: CanvasRenderingContext2D, scale: number) => {
    if (node.id === "baby_core") { drawGauge(node, ctx, scale); return; }

    const type = String(node.type);
    const r = RADIUS[type] ?? 7;
    let color = cssVar(TYPE_VAR[type] ?? "--node-infra");
    let ghosted = false;

    if (type === "brain") {
      const daily = node.id === "brain:daily";
      if (gameRef.current && daily) {
        ghosted = true; // game mode unloads the local 9B
      } else if (!daily) {
        if (routerRef.current === "offline") color = cssVar("--red");
        else if (routerRef.current === "degraded") color = cssVar("--amber");
      }
    }

    ctx.globalAlpha = ghosted ? 0.28 : 1;
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();
    if (ghosted) {
      ctx.setLineDash([3 / scale, 3 / scale]);
      ctx.lineWidth = 1 / scale;
      ctx.strokeStyle = cssVar("--faint");
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.globalAlpha = 1;

    // active-brain highlight
    if (type === "brain" && node.id === activeRef.current) {
      ctx.lineWidth = 2 / scale;
      ctx.strokeStyle = cssVar("--node-brain");
      ctx.beginPath(); ctx.arc(node.x, node.y, r + 3 / scale, 0, 2 * Math.PI); ctx.stroke();
    }

    // transient error/confirm flash
    const fl = flashes.current.get(node.id);
    if (fl && now() < fl.expiry) {
      ctx.lineWidth = 3 / scale;
      ctx.strokeStyle = cssVar(PULSE_VAR[fl.klass]);
      ctx.beginPath(); ctx.arc(node.x, node.y, r + 5 / scale, 0, 2 * Math.PI); ctx.stroke();
    }

    // selection ring
    if (node.id === selRef.current) {
      ctx.lineWidth = 2 / scale;
      ctx.strokeStyle = "#e6e9ef";
      ctx.beginPath(); ctx.arc(node.x, node.y, r + 4 / scale, 0, 2 * Math.PI); ctx.stroke();
    }

    const big = type === "brain" || type === "router" || type === "safety";
    if (big || scale > 1.6) label(ctx, String(node.label), node.x, node.y + r + 2 / scale, scale);
  };

  const paintPointer = (node: any, color: string, ctx: CanvasRenderingContext2D) => {
    const r = (RADIUS[String(node.type)] ?? 7) + 3;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
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
          autoPauseRedraw={false}
          cooldownTicks={0}
          warmupTicks={80}
          enableNodeDrag={false}
          nodeCanvasObject={drawNode}
          nodePointerAreaPaint={paintPointer}
          nodeLabel={(n: any) => `${n.label} — ${n.role || n.type}`}
          linkColor={() => edgeColor}
          linkWidth={1}
          linkDirectionalParticleWidth={3}
          linkDirectionalParticleSpeed={0.02}
          linkDirectionalParticleColor={(l: any) => cssVar(PULSE_VAR[(l.__pulseKlass as PulseClass) ?? "normal"])}
          onNodeClick={(n: any) => useBrain.getState().selectNode(String(n.id))}
          onBackgroundClick={() => useBrain.getState().selectNode(null)}
          onEngineStop={() => { fgRef.current?.zoomToFit(400, 50); startClock(); }}
        />
      )}
    </div>
  );
}
