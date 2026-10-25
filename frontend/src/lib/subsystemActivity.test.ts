// @ts-expect-error The app intentionally does not ship Node typings; Vitest runs in Node.
import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";
import { attachRepoLabels } from "./subsystemActivity";
import type { BackgroundActivityItem } from "../api/activity";

const base: BackgroundActivityItem = {
  id: "download:t1", kind: "model_download", title: "模型下载", label: "flux.safetensors",
  status: "running", progress: "5.0 MB / 10.0 MB", started_at: 1, updated_at: 2,
};

describe("attachRepoLabels", () => {
  it("只给带 repo_id 的活动补显示名", () => {
    const items = attachRepoLabels(
      [base, { ...base, id: "maintenance:repo-1", kind: "chat_maintenance", repo_id: "repo-1" }],
      (id) => (id === "repo-1" ? "大仓库 · 小仓库" : id),
    );
    expect(items[0].repoLabel).toBeUndefined();
    expect(items[1].repoLabel).toBe("大仓库 · 小仓库");
  });

  it("不修改入参数组（轮询快照可安全复用）", () => {
    const source = [{ ...base, repo_id: "repo-1" }];
    attachRepoLabels(source, () => "仓库");
    expect((source[0] as { repoLabel?: string }).repoLabel).toBeUndefined();
  });
});

describe("后台活动轮询契约", () => {
  const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

  it("新活动源走 /activity/background 且带超时", () => {
    const api = read("../api/activity.ts");
    expect(api).toContain('apiGet<{ items: BackgroundActivityItem[] }>("/activity/background", POLL_TIMEOUT_MS)');
    expect(api).toContain("const POLL_TIMEOUT_MS = 8000");
  });

  it("面板其余四个轮询源都必须带超时（无超时会让轮询链静默断掉）", () => {
    const jobs = read("../api/jobs.ts");
    expect(jobs).toContain("POLL_TIMEOUT_MS");
    // 2026-10-24 api/ai 按资源域拆分：running-threads 在 ai/agent.ts，chat-queue 在 ai/chatQueue.ts
    const agentApi = read("../api/ai/agent.ts");
    expect(agentApi).toContain('"/ai/image-agent/running-threads", POLL_TIMEOUT_MS');
    const chatQueueApi = read("../api/ai/chatQueue.ts");
    expect(chatQueueApi).toContain("POLL_TIMEOUT_MS,");
    const plans = read("./planTaskActivity.ts");
    expect(plans).toContain('apiGet<PlanTask[]>("/plans?limit=10", 8000)');
  });

  it("SupportWidget 订阅新活动源并渲染", () => {
    const widget = read("../components/SupportWidget.tsx");
    expect(widget).toContain("subscribeSubsystemActivities");
    expect(widget).toContain("subsystemActivities.map");
    expect(widget).toContain("onOpenSettings");
  });
});
