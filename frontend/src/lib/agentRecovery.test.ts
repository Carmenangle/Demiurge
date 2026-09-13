import { describe, expect, it, vi } from "vitest";
import { recoverAgentRun, shouldRecoverAgentRun } from "./agentRecovery";

describe("agent background recovery", () => {
  it("never restores the shared home thread into the temporary homepage draft", () => {
    expect(shouldRecoverAgentRun("home")).toBe(false);
    expect(shouldRecoverAgentRun("repo-1")).toBe(true);
  });

  it("keeps polling after the stream disconnects and returns the completed snapshot", async () => {
    const snapshots = [
      { items: [{ id: "running", role: "assistant" as const, text: "生成中" }] },
      { items: [{ id: "image-1", role: "assistant" as const, text: "", image: "local://result.png" }] },
    ];
    const states = [{ running: true }, { running: false }];
    const onSnapshot = vi.fn();
    const wait = vi.fn(async () => {});

    const settled = await recoverAgentRun({
      fetchSnapshot: async () => snapshots.shift()!,
      fetchRunning: async () => states.shift()!,
      onSnapshot,
      isActive: () => true,
      wait,
    });

    expect(settled).toBe(true);
    expect(onSnapshot).toHaveBeenCalledTimes(2);
    expect(onSnapshot.mock.calls[1][0][0].image).toBe("local://result.png");
    expect(wait).toHaveBeenCalledTimes(1);
  });

  it("retries a temporary snapshot failure instead of abandoning the upstream task", async () => {
    let attempt = 0;
    const onSnapshot = vi.fn();

    const settled = await recoverAgentRun({
      fetchSnapshot: async () => {
        attempt += 1;
        if (attempt === 1) throw new Error("network interrupted");
        return { items: [{ id: "image-1", role: "assistant" as const, text: "", image: "local://result.png" }] };
      },
      fetchRunning: async () => ({ running: false }),
      onSnapshot,
      isActive: () => true,
      wait: async () => {},
    });

    expect(settled).toBe(true);
    expect(onSnapshot).toHaveBeenCalledTimes(1);
  });
});
