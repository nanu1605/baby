/**
 * The signup-link call (v6.0.2).
 *
 * The wizard's "Get a <provider> key" links were plain `<a target="_blank">`.
 * Inside the shell that is a WebView2 window with no new-window handler, so the
 * click was silently dropped — on the one step where a user without a key has to
 * leave the app. The link now asks the backend to open the page.
 *
 * The contract worth pinning is what gets SENT: an env name and nothing else. The
 * backend resolves the destination from its own frozen list, and a caller that
 * could name a URL would turn a local port into "open any page in this user's
 * browser". Component behaviour stays untested by design (see vitest.config.ts).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { postKeySignup } from "./client";

function stubFetch(res: { ok: boolean; json: () => unknown }) {
  const spy = vi.fn().mockResolvedValue({
    ok: res.ok,
    json: async () => res.json(),
  } as unknown as Response);
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("postKeySignup", () => {
  it("posts the env name and nothing that could redirect it", async () => {
    const spy = stubFetch({ ok: true, json: () => ({ opened: true }) });

    await postKeySignup("OPENROUTER_API_KEY");

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/setup/keys/signup");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body as string);
    expect(sent).toEqual({ env: "OPENROUTER_API_KEY" });
    // Belt and braces: no destination may ride along under any name.
    expect(JSON.stringify(sent)).not.toMatch(/https?:\/\//);
  });

  it("reports whether the browser actually opened", async () => {
    stubFetch({ ok: true, json: () => ({ opened: true }) });
    expect(await postKeySignup("GEMINI_API_KEY")).toBe(true);

    stubFetch({ ok: true, json: () => ({ opened: false }) });
    expect(await postKeySignup("GEMINI_API_KEY")).toBe(false);
  });

  it("is false on a rejected request, so the caller can fall back", async () => {
    // The body deliberately claims success. A failed response's body is not
    // evidence of anything, and only the status check catches that — a version
    // of this test using {error: ...} passed with the check deleted, because
    // `undefined === true` is false for the wrong reason.
    stubFetch({ ok: false, json: () => ({ opened: true }) });
    expect(await postKeySignup("SECRET_KEY")).toBe(false);
  });
});
