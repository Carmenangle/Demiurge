import { describe, expect, it } from "vitest";
import {
  beginNewSession, beginRestore, completeRestore, initialBuildSessionModel,
  markSaved, canAutosave,
} from "./buildSessionModel";

describe("build session model", () => {
  it("pending/new session always clears the previous identity", () => {
    const active = { generation: 2, phase: "active" as const, sessionId: "old", graphLoaded: true };
    expect(beginNewSession(active)).toEqual({ generation: 3, phase: "empty", sessionId: "", graphLoaded: false });
  });

  it("ignores stale restore and save results", () => {
    const restoring = beginRestore(initialBuildSessionModel);
    const newer = beginRestore(restoring);
    expect(completeRestore(newer, restoring.generation, "old")).toBe(newer);
    expect(markSaved(newer, restoring.generation, "old")).toBe(newer);
  });

  it("only autosaves an active loaded graph", () => {
    const restoring = beginRestore(initialBuildSessionModel);
    expect(canAutosave(restoring, true, true)).toBe(false);
    const active = completeRestore(restoring, restoring.generation, "s1");
    expect(canAutosave(active, true, true)).toBe(true);
  });
});
