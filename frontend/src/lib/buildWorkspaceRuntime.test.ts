import { describe, expect, it } from "vitest";
import { BuildWorkspaceRuntime } from "./buildWorkspaceRuntime";
import type { WorkflowBuildActivity } from "./workflowBuildActivity";

function storage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
  };
}

const terminal = (id: string): WorkflowBuildActivity => ({
  id, sessionId: "session-1", mode: "direct", need: "build", status: "done",
  createdAt: 1, updatedAt: 2,
});

describe("build workspace lifecycle", () => {
  it("ignores stale restores and remembers the winning session", () => {
    const store = storage();
    const runtime = new BuildWorkspaceRuntime(store);
    const old = runtime.startRestore();
    const current = runtime.startRestore();
    expect(runtime.finishRestore(old, "old")).toBe(false);
    expect(runtime.finishRestore(current, "current")).toBe(true);
    expect(runtime.lastSessionId()).toBe("current");
  });

  it("claims each terminal activity once until it is completed", () => {
    const runtime = new BuildWorkspaceRuntime(storage());
    const activity = terminal("task-1");
    expect(runtime.claimTerminal([activity], "session-1")).toEqual([activity]);
    expect(runtime.claimTerminal([activity], "session-1")).toEqual([]);
    runtime.completeActivity("session-1", activity.id);
    expect(runtime.claimTerminal([activity], "session-1")).toEqual([]);
  });

  it("releases a failed reload so the terminal activity can retry", () => {
    const runtime = new BuildWorkspaceRuntime(storage());
    const activity = terminal("task-1");
    runtime.claimTerminal([activity], "session-1");
    runtime.releaseActivity(activity.id);
    expect(runtime.claimTerminal([activity], "session-1")).toEqual([activity]);
  });
});
