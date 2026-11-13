import { describe, expect, it, vi } from "vitest";
import {
  branchChatState, branchMediaInsertPresets, completedTurn, createScenarioBranch,
  type ScenarioBranchDeps,
} from "./scenarioBranchRuntime";

function deps(overrides: Partial<ScenarioBranchDeps> = {}): ScenarioBranchDeps {
  return {
    saveMessages: vi.fn(async () => undefined),
    listSnapshots: vi.fn(async () => ({ items: [] })),
    createSnapshot: vi.fn(async (input) => ({
      snapshot_id: "snapshot", source_repo_id: input.repo_id,
      turn: input.turn, created_at: 1,
    })),
    forkSnapshot: vi.fn(async () => undefined),
    addBranch: vi.fn((_parent, _binding, id) => id),
    openWork: vi.fn(),
    persistMessages: vi.fn(),
    newId: () => "child",
    ...overrides,
  };
}

describe("scenario branch runtime", () => {
  it("counts only completed assistant turns", () => {
    expect(completedTurn([
      { role: "user", text: "one" }, { role: "assistant", text: "reply" },
      { role: "assistant", text: "  " }, { role: "tool", text: "ignored" },
    ])).toBe(1);
  });

  it("creates a latest-turn snapshot before forking and committing local state", async () => {
    const runtime = deps();
    const result = await createScenarioBranch({
      outputDir: "output", sourceRepoId: "source", parentId: "parent",
      binding: { cardName: "顾凝霜" }, messages: [{ role: "assistant", text: "reply" }],
      isLatest: true,
    }, runtime);

    expect(result.status).toBe("created");
    expect(runtime.saveMessages).toHaveBeenCalled();
    expect(runtime.createSnapshot).toHaveBeenCalledWith(expect.objectContaining({ turn: 1 }));
    expect(runtime.forkSnapshot).toHaveBeenCalledWith(expect.objectContaining({ target_repo_id: "child" }));
    expect(runtime.addBranch).toHaveBeenCalledWith("parent", expect.anything(), "child");
    expect(runtime.openWork).toHaveBeenCalledWith("parent", "child");
  });

  it("refuses a historical branch without a matching world-state snapshot", async () => {
    const runtime = deps();
    const result = await createScenarioBranch({
      outputDir: "output", sourceRepoId: "source", parentId: "parent",
      binding: {}, messages: [{ role: "assistant", text: "reply" }], isLatest: false,
    }, runtime);

    expect(result).toEqual({ status: "missing_snapshot", turn: 1 });
    expect(runtime.createSnapshot).not.toHaveBeenCalled();
    expect(runtime.forkSnapshot).not.toHaveBeenCalled();
    expect(runtime.addBranch).not.toHaveBeenCalled();
  });

  it("copies media insert presets to the new branch without sharing nested config", () => {
    const presets = {
      source: { templateId: "t", characterLoras: { 甲: { loraName: "a.safetensors" } } },
    };
    const next = branchMediaInsertPresets(presets, "source", "child");

    expect(next?.child).toEqual(presets.source);
    expect(next?.child).not.toBe(presets.source);
    expect(next?.child.characterLoras).not.toBe(presets.source.characterLoras);
    expect(presets).not.toHaveProperty("child");
    expect(branchMediaInsertPresets(undefined, "source", "child")).toBeNull();
    expect(branchMediaInsertPresets({ other: { templateId: "" } }, "source", "child")).toBeNull();
  });

  it("carries chat avatars and roster overrides to the branch", () => {
    const avatars = { 甲: "avatar.png" };
    const roster = { mode: "worldbook" as const, include: ["u1"] };
    const patch = branchChatState({ characterAvatars: avatars, characterRoster: roster });

    expect(patch.characterAvatars).toEqual(avatars);
    expect(patch.characterAvatars).not.toBe(avatars);
    expect(patch.characterRoster).toEqual(roster);
    expect(patch.characterRoster).not.toBe(roster);
    expect(branchChatState({})).toEqual({});
  });
});
