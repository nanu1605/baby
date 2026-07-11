import { useEffect, useState } from "react";

/**
 * One shared prefers-reduced-motion subscription (V4, consolidates Decision #116).
 * Before V4 this was read three ad-hoc ways — Scene/CoreGauge snapshot matchMedia once,
 * BrainGraph listened for `change`. This is the single source: reads the current value
 * and stays live if the OS setting flips mid-session.
 */
const QUERY = "(prefers-reduced-motion: reduce)";

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia &&
      window.matchMedia(QUERY).matches,
  );

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(QUERY);
    const onChange = () => setReduced(mq.matches);
    onChange(); // reconcile any flip in the render→effect gap before subscribing
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return reduced;
}
