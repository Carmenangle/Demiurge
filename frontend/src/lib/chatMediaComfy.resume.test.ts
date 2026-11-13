import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  pollWorkflowResult: vi.fn(),
  saveLocal: vi.fn(),
  submitWorkflow: vi.fn(),
  interruptComfy: vi.fn(),
  localViewUrl: vi.fn((path: string) => `local:${path}`),
}));

vi.mock("../api/comfyui", () => ({
  localViewUrl: mocks.localViewUrl,
  saveLocal: mocks.saveLocal,
  submitWorkflow: mocks.submitWorkflow,
  interruptComfy: mocks.interruptComfy,
}));

vi.mock("./workflowGenerationRuntime", () => ({
  pollWorkflowResult: mocks.pollWorkflowResult,
}));

import { resumeChatMediaImage } from "./chatMediaComfy";

describe("resumeChatMediaImage", () => {
  beforeEach(() => {
    mocks.pollWorkflowResult.mockReset();
    mocks.saveLocal.mockReset();
    mocks.submitWorkflow.mockReset();
    mocks.interruptComfy.mockReset();
  });

  it("刷新后只轮询已有 promptId 并落盘，不重新提交", async () => {
    mocks.pollWorkflowResult.mockResolvedValue({
      kind: "complete",
      result: {
        status: "completed",
        images: [{ filename: "a.png", subfolder: "", type: "output" }],
        videos: [], audios: [], texts: [],
      },
    });
    mocks.saveLocal.mockResolvedValue({ path: "D:/out/a.png" });

    const out = await resumeChatMediaImage({
      promptId: "p1",
      prompt: "海边泳装",
      repoId: "repo-1",
      outputDir: "D:/out",
      comfyUrl: "http://comfy",
    });

    expect(mocks.pollWorkflowResult).toHaveBeenCalledWith(
      "p1", "http://comfy", "image", { timeoutMs: undefined },
    );
    expect(mocks.submitWorkflow).not.toHaveBeenCalled();
    expect(mocks.saveLocal).toHaveBeenCalledTimes(1);
    expect(out).toEqual({
      url: "local:D:/out/a.png", path: "D:/out/a.png", prompt: "海边泳装",
    });
  });

  it("没有 promptId 时不轮询，直接报错", async () => {
    await expect(resumeChatMediaImage({
      promptId: "",
      prompt: "海边泳装",
      repoId: "repo-1",
      outputDir: "D:/out",
      comfyUrl: "http://comfy",
    })).rejects.toThrow("没有可续接的拍摄任务");
    expect(mocks.pollWorkflowResult).not.toHaveBeenCalled();
  });
});
