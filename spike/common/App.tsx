// V0 shell spike — the shared app: full-bleed 3D canvas + a HUD (live fps,
// countdown, final result) + the Motion buttons. Byte-identical across shells;
// each shell mounts this exact component. The only per-shell difference is the
// injected window.spikeAPI (result/screenshot persistence + shell cold-start).

import { useEffect, useMemo, useRef, useState } from "react";
import { SpikeCanvas } from "./scene";
import { SpikeButtons } from "./buttons";
import { SpikeHarness, WINDOW_S } from "./harness";
import type { SpikeResult } from "./spikeApi";

export function SpikeApp({ shell }: { shell: SpikeResult["shell"] }) {
  const [result, setResult] = useState<SpikeResult | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const harness = useMemo(() => new SpikeHarness(shell, setResult), [shell]);
  const startedAt = useRef<number>(0);

  useEffect(() => {
    harness.start();
    startedAt.current = performance.now();
    const t = setInterval(() => {
      setElapsed(Math.min(WINDOW_S, (performance.now() - startedAt.current) / 1000));
    }, 250);
    return () => clearInterval(t);
  }, [harness]);

  return (
    <div style={{ position: "fixed", inset: 0, background: "#0b0d12" }}>
      <SpikeCanvas harness={harness} />

      <div style={hud}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>
          spike · {shell} {result ? "· done" : `· ${Math.round(elapsed)}/${WINDOW_S}s`}
        </div>
        {result ? (
          <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.4 }}>
            {`fps p50      ${result.fps_p50}
fps 1% low   ${result.fps_1pct_low}
fps avg      ${result.fps_avg}
frames       ${result.frame_count}
cold(render) ${result.cold_start_render_ms} ms
cold(shell)  ${result.cold_start_shell_ms ?? "n/a"} ms
VRAM used    ${result.vram_used_gb ?? "n/a"} / ${result.vram_total_gb ?? "n/a"} GB
VRAM min/max ${result.vram_used_gb_min ?? "n/a"} / ${result.vram_used_gb_max ?? "n/a"} GB
GPU util max ${result.gpu_util_max ?? "n/a"} %
GPU          ${result.gpu_name ?? "n/a"}
saved → result.json + screenshot.png`}
          </pre>
        ) : (
          <div style={{ fontSize: 12, opacity: 0.8 }}>
            measuring… hold still, fixed camera path running
          </div>
        )}
        <div style={{ marginTop: 10 }}>
          <SpikeButtons />
        </div>
      </div>
    </div>
  );
}

const hud: React.CSSProperties = {
  position: "fixed",
  top: 14,
  left: 14,
  padding: "12px 14px",
  borderRadius: 12,
  background: "rgba(11,13,18,0.72)",
  border: "1px solid #2a2f3a",
  color: "#e6e9ef",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  backdropFilter: "blur(6px)",
  maxWidth: 360,
};
