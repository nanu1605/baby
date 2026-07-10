import { useEffect } from "react";
import { useBrain } from "../store";
import { motionLevel } from "../graph/motion";
import { useReducedMotion } from "./useReducedMotion";

/**
 * Publish the decorative-motion level to `<body data-motion>` (V4) so plain CSS can gate
 * transitions + keyframes off the governor tier and the performanceMode opt-in — not just
 * OS reduced-motion, which was the only signal that reached CSS before. Also mirrors the
 * live pipeline state to `<body data-pstate>` so chrome accents can track it via
 * `--accent-live` (cohesion with the sphere gauge). No render cost — effects write
 * `document.body.dataset` only.
 */
export function useMotionFlag(): void {
  const reduced = useReducedMotion();
  const performanceMode = useBrain((s) => s.performanceMode);
  const renderTier = useBrain((s) => s.renderTier);
  const pipeline = useBrain((s) => s.pipeline);

  useEffect(() => {
    document.body.dataset.motion = motionLevel(reduced, performanceMode, renderTier);
  }, [reduced, performanceMode, renderTier]);

  useEffect(() => {
    document.body.dataset.pstate = pipeline;
  }, [pipeline]);
}
