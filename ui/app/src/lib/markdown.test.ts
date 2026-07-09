import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown";

describe("renderMarkdown — defense in depth", () => {
  it("renders basic markdown", () => {
    expect(renderMarkdown("**bold**")).toContain("<strong>bold</strong>");
  });

  it("layer 1: drops raw HTML the model emits", () => {
    const html = renderMarkdown('hi <img src=x onerror=alert(1)> <b>raw</b>');
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("<img");
  });

  it("layer 2: sanitizes a script tag", () => {
    expect(renderMarkdown("<script>alert(1)</script>")).not.toContain("<script");
  });

  it("hardens links with rel + target", () => {
    const html = renderMarkdown("[x](https://example.com)");
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain('target="_blank"');
  });
});
