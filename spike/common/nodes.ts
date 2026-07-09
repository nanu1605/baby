// V0 shell spike — deterministic node/arc topology.
// Byte-identical across the Tauri + Electron spikes (both import this verbatim),
// so fps/VRAM/bloom compare apples-to-apples. This is NOT the real /api/graph —
// it is a fixed ~40-node stand-in sized like the real brain graph, purely to
// stress the renderer. The real sphere topology lands in V3.

export interface SpikeNode {
  id: number;
  x: number;
  y: number;
  z: number;
  hue: number; // 0..1, drives emissive colour
}

export interface SpikeArc {
  a: number; // node index
  b: number; // node index
}

const NODE_COUNT = 42; // ~ the real subsystem+tool+brain count
const RADIUS = 3.2;

// Fibonacci sphere — deterministic, evenly distributed, no RNG (RNG is banned in
// this codebase's deterministic paths and would break byte-identical comparison).
function fibonacciSphere(n: number, r: number): SpikeNode[] {
  const out: SpikeNode[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5)); // ~2.399963
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2; // 1 .. -1
    const radiusAtY = Math.sqrt(1 - y * y);
    const theta = golden * i;
    const x = Math.cos(theta) * radiusAtY;
    const z = Math.sin(theta) * radiusAtY;
    out.push({
      id: i,
      x: x * r,
      y: y * r,
      z: z * r,
      hue: (i / n) % 1,
    });
  }
  return out;
}

export const NODES: SpikeNode[] = fibonacciSphere(NODE_COUNT, RADIUS);

// Deterministic arc set: each node connects to its "golden neighbour" a fixed
// stride away, plus a few cross-sphere long arcs — ~50 arcs, enough to exercise
// the bloom-lit line rendering that the real turn path will use.
export const ARCS: SpikeArc[] = (() => {
  const arcs: SpikeArc[] = [];
  const n = NODES.length;
  for (let i = 0; i < n; i++) {
    arcs.push({ a: i, b: (i + 5) % n });
    if (i % 3 === 0) arcs.push({ a: i, b: (i + 17) % n });
  }
  return arcs;
})();
