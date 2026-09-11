import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { captureWorkflowApiPrompt, requestFrameMessage } from "./workflowCapture";

const ORIGIN = "http://127.0.0.1:8188";

function fakeBrowser() {
  const listeners = new Map<string, Set<(event: any) => void>>();
  const posts: any[] = [];
  const frameWindow = {
    postMessage: (message: unknown, targetOrigin: string) => posts.push({ message, targetOrigin }),
  } as unknown as Window;
  const loadListeners: Array<() => void> = [];
  const frame = {
    style: { cssText: "" }, src: "", contentWindow: frameWindow,
    addEventListener: (type: string, fn: () => void) => { if (type === "load") loadListeners.push(fn); },
    remove: vi.fn(),
  } as unknown as HTMLIFrameElement;
  vi.stubGlobal("window", {
    addEventListener: (type: string, fn: (event: any) => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(fn);
    },
    removeEventListener: (type: string, fn: (event: any) => void) => listeners.get(type)?.delete(fn),
  });
  vi.stubGlobal("document", {
    createElement: () => frame,
    body: { appendChild: vi.fn() },
  });
  return {
    posts, frame, frameWindow,
    dispatch: (type: string, payload: unknown = {}) => {
      const event = { source: frameWindow, origin: ORIGIN, data: { source: "laf_lock", type, payload } };
      listeners.get("message")?.forEach((fn) => fn(event));
    },
    load: () => loadListeners.forEach((fn) => fn()),
  };
}

describe("workflow capture transaction", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("loads, applies ops, then captures the native prompt", async () => {
    const browser = fakeBrowser();
    const result = captureWorkflowApiPrompt({
      workflow: { nodes: [{ id: 1 }] }, comfyUrl: ORIGIN, ops: [{ node_id: "1" }],
    });
    browser.dispatch("ready");
    expect(browser.posts[browser.posts.length - 1]?.message.type).toBe("load");
    browser.dispatch("loaded");
    await vi.advanceTimersByTimeAsync(300);
    expect(browser.posts[browser.posts.length - 1]?.message.type).toBe("apply_ops");
    browser.dispatch("ops_applied", { results: [{ ok: true }] });
    await vi.advanceTimersByTimeAsync(300);
    expect(browser.posts[browser.posts.length - 1]?.message.type).toBe("request_api_prompt");
    browser.dispatch("api_prompt", {
      ok: true, output: { "1": {} }, workflow: { nodes: [{ id: 2 }] },
    });
    await expect(result).resolves.toEqual({
      prompt: { "1": {} }, workflow: { nodes: [{ id: 2 }] }, opResults: [{ ok: true }],
    });
    expect(browser.frame.remove).toHaveBeenCalledOnce();
  });

  it("retries native capture and finishes with a null prompt", async () => {
    const browser = fakeBrowser();
    const result = captureWorkflowApiPrompt({ workflow: { nodes: [] }, comfyUrl: ORIGIN });
    browser.dispatch("ready");
    browser.dispatch("loaded");
    await vi.advanceTimersByTimeAsync(300);
    for (let index = 0; index < 4; index += 1) {
      browser.dispatch("api_prompt", { ok: false });
      if (index < 3) await vi.advanceTimersByTimeAsync(600);
    }
    await expect(result).resolves.toMatchObject({ prompt: null });
  });

  it("supports a single request/reply through the same frame seam", async () => {
    const browser = fakeBrowser();
    const result = requestFrameMessage<{ output: object }>({
      frameWindow: browser.frameWindow, comfyUrl: ORIGIN,
      requestType: "request_api_prompt", expectedType: "api_prompt", timeoutMs: 1000,
    });
    expect(browser.posts[browser.posts.length - 1]?.message.type).toBe("request_api_prompt");
    browser.dispatch("api_prompt", { output: { "1": {} } });
    await expect(result).resolves.toEqual({ output: { "1": {} } });
  });
});
