// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const arts = [
  { kind: "card", name: "角色主卡 · 御仙", path: "D:/w/御仙/card.json", size: 146172 },
  { kind: "worldbook", name: "世界书 · 御仙", path: "D:/w/御仙/worldbook.json", size: 129483 },
];

vi.mock("dompurify", () => ({ default: { sanitize: (html: string) => html } }));
vi.mock("../../lib/planTaskActivity", () => ({
  approvePlanTask: vi.fn(),
  cancelPlanTask: vi.fn(),
  getPlanTask: vi.fn(async () => ({ id: "task-1", status: "done", progress: "4/4 步", steps: [] })),
  keepRecipe: vi.fn(),
  deleteRecipe: vi.fn(),
  listRecipes: vi.fn(async () => ({})),
}));
vi.mock("../../api/artifacts", () => ({
  previewArtifact: vi.fn(),
  artifactDownloadUrl: vi.fn(() => ""),
  openArtifactFolder: vi.fn(),
  listArtifacts: vi.fn(async () => ({ ok: true, items: arts })),
  listVersions: vi.fn(async () => ({ ok: true, versions: [] })),
  restoreVersion: vi.fn(),
  deleteVersion: vi.fn(),
  syncWorldbook: vi.fn(),
}));

import { AssistantMessage } from "./ChatMessages";
import { getPlanTask, type PlanTask } from "../../lib/planTaskActivity";
import { listArtifacts } from "../../api/artifacts";

let container: HTMLDivElement;
let root: Root;
async function mount(node: React.ReactNode) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(node); });
}
afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.clearAllMocks();
});

describe("计划任务终态产物卡（2026-09-09 实锤：批准执行后对话里没有任何结果展示）", () => {
  it("消息含 [[plan:]] 且任务已 done → 主动查状态并渲染交付产物卡", async () => {
    await mount(
      <AssistantMessage
        msg={{ id: "assistant-plan-1", role: "assistant", text: "已编译计划…\n\n[[plan:task-1]]" }}
        visualCiOutputDir="D:/w"
        visualCiRepoId="repo-1"
        onSendImage={() => {}}
      />,
    );
    expect(getPlanTask).toHaveBeenCalledWith("task-1");
    expect(listArtifacts).toHaveBeenCalledWith("D:/w", "repo-1");
    const text = container.textContent || "";
    expect(text).toContain("交付产物");
    expect(text).toContain("御仙");
  });

  it("任务未完成（awaiting_approval）时不渲染产物卡、不拉产物", async () => {
    // 终态恢复 effect 与「边跑边看」effect 挂载时都会查一次任务（生产同刻结果一致）
    vi.mocked(getPlanTask).mockResolvedValue({
      id: "task-2", status: "awaiting_approval", progress: "0/4 步", steps: [],
    } as never);
    await mount(
      <AssistantMessage
        msg={{ id: "assistant-plan-2", role: "assistant", text: "待批准\n\n[[plan:task-2]]" }}
        visualCiOutputDir="D:/w"
        visualCiRepoId="repo-1"
        onSendImage={() => {}}
      />,
    );
    expect(listArtifacts).not.toHaveBeenCalled();
    expect(container.textContent || "").not.toContain("交付产物");
  });

  it("没有 [[plan:]] 标记的普通消息不查询、不渲染", async () => {
    await mount(
      <AssistantMessage
        msg={{ id: "assistant-plain", role: "assistant", text: "普通回复" }}
        visualCiOutputDir="D:/w"
        visualCiRepoId="repo-1"
        onSendImage={() => {}}
      />,
    );
    expect(getPlanTask).not.toHaveBeenCalled();
    expect(container.textContent || "").not.toContain("交付产物");
  });
});

describe("计划任务运行中已完成缩略图（2026-09-09 方向4：放宽产物卡 done/partial 门）", () => {
  const runningTask: PlanTask = {
    id: "task-run", status: "running", progress: "3/4 步 · 图 2/14", error: "",
    created_at: 1, updated_at: 1, repo_id: "repo-1", output_dir: "D:/w", intent: "批量出图",
    steps: [{
      seq: 2, step_id: "s4", operation: "media.collect_comfy_outputs", status: "done",
      attempts: 0, last_error: "",
      outputs: { results: [{ url: "local://01-a.png" }, { url: "local://02-b.png" }] },
    }],
  };
  const doneTask: PlanTask = { ...runningTask, status: "done" };

  it("运行中任务 → 轮询把已完成产物以缩略图展示在消息下（不渲染交付卡、不拉产物）", async () => {
    vi.mocked(getPlanTask).mockResolvedValue(runningTask);
    await mount(
      <AssistantMessage
        msg={{ id: "assistant-run", role: "assistant", text: "执行中…\n\n[[plan:task-run]]" }}
        visualCiOutputDir="D:/w"
        visualCiRepoId="repo-1"
        onSendImage={() => {}}
      />,
    );
    await act(async () => { await Promise.resolve(); });  // 冲刷首轮 getPlanTask 微任务
    const thumbs = container.querySelectorAll("img[alt='生成图']");
    expect(thumbs.length).toBe(2);
    expect(thumbs[0].getAttribute("src")).toBe("local://01-a.png");
    expect(listArtifacts).not.toHaveBeenCalled();          // running 不拉交付产物
    expect(container.textContent || "").not.toContain("交付产物");
  });

  it("任务推进到 done → 缩略图让位交付产物卡（不重复展示两遍）", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getPlanTask).mockResolvedValue(runningTask);
      await mount(
        <AssistantMessage
          msg={{ id: "assistant-done", role: "assistant", text: "执行中…\n\n[[plan:task-run]]" }}
          visualCiOutputDir="D:/w"
          visualCiRepoId="repo-1"
          onSendImage={() => {}}
        />,
      );
      await act(async () => { await Promise.resolve(); });
      expect(container.querySelectorAll("img[alt='生成图']").length).toBe(2);

      vi.mocked(getPlanTask).mockResolvedValue(doneTask);
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });  // 下一轮：done
      await act(async () => { await Promise.resolve(); });                  // 冲刷 autoLoad
      expect(container.querySelectorAll("img[alt='生成图']").length).toBe(0); // 缩略图让位
      expect(listArtifacts).toHaveBeenCalledWith("D:/w", "repo-1");
      expect(container.textContent || "").toContain("交付产物");
    } finally {
      vi.useRealTimers();
    }
  });
});