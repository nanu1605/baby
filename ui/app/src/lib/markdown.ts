/**
 * Chat markdown renderer — owner-hardened, defense-in-depth (B2).
 *
 * Applied ONLY to the final authoritative `reply` on turn_end. Streaming tokens
 * are rendered as plain text via React (`textContent` semantics), never through
 * this path — so a partial/live stream can never inject markup, and there is no
 * partial-markdown flicker.
 *
 * Layer 1 — marked with raw HTML disabled: the `html` renderer is overridden to
 * drop raw HTML blocks/inline entirely, so markdown is treated as markdown only.
 * Layer 2 — DOMPurify sanitizes marked's output before it reaches the DOM.
 * Links get rel="noopener noreferrer" + target="_blank" via a DOMPurify hook.
 */
import { marked } from "marked";
import DOMPurify from "dompurify";

// Layer 1: neutralize any raw HTML the model emits inside a reply.
marked.use({
  gfm: true,
  breaks: false,
  renderer: {
    html: () => "",
  },
});

// Harden every anchor the sanitizer keeps.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("rel", "noopener noreferrer");
    node.setAttribute("target", "_blank");
  }
});

export function renderMarkdown(src: string): string {
  const raw = marked.parse(src ?? "", { async: false }) as string;
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
}
