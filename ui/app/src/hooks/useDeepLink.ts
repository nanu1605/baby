import { useEffect } from "react";
import { useBrain } from "../store";

/**
 * Two-way sync between the URL hash `#node/<id>` and the selected node (B4).
 * Read the hash on mount + on `hashchange` → select that node (camera fly-to +
 * drawer follow from the store). Write the hash when the selection changes, via
 * `replaceState` so it doesn't spam browser history. B5's search reuses this.
 */
export function useDeepLink(): void {
  useEffect(() => {
    const applyHash = () => {
      const m = location.hash.match(/^#node\/(.+)$/);
      if (m) useBrain.getState().selectNode(decodeURIComponent(m[1]));
    };
    applyHash();
    window.addEventListener("hashchange", applyHash);

    let last = useBrain.getState().selectedNode;
    const unsub = useBrain.subscribe((s) => {
      if (s.selectedNode === last) return;
      last = s.selectedNode;
      const want = last ? `#node/${encodeURIComponent(last)}` : "";
      if (location.hash !== want) {
        history.replaceState(null, "", want || location.pathname + location.search);
      }
    });

    return () => {
      window.removeEventListener("hashchange", applyHash);
      unsub();
    };
  }, []);
}
