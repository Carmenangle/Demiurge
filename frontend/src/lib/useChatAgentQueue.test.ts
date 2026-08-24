// useChatAgentQueue 的 settled 检测：队列任务从活跃变终态时通知外部刷新对话。
import { describe, expect, it } from "vitest";
import { activeTaskIds, settledTaskIds } from "./useChatAgentQueue";
import type { ChatQueueTask, ChatQueueStatus } from "../api/ai";

const task = (id: string, status: ChatQueueStatus): ChatQueueTask => ({
  id, thread_id: "repo-1", need: "n", status,
  created_at: 1, updated_at: 1,
});

describe("activeTaskIds", () => {
  it("只统计 queued/running，忽略终态", () => {
    const ids = activeTaskIds([
      task("a", "queued"), task("b", "running"), task("c", "done"), task("d", "cancelled"),
    ]);
    expect([...ids].sort()).toEqual(["a", "b"]);
  });
});

describe("settledTaskIds", () => {
  it("任务从活跃变终态时返回该任务 id", () => {
    const prev = new Set(["a", "b"]);
    const settled = settledTaskIds(prev, [task("a", "done"), task("b", "running")]);
    expect(settled).toEqual(["a"]);
  });

  it("无变化（任务仍活跃或仍终态）时不返回", () => {
    const prev = new Set(["a"]);
    expect(settledTaskIds(prev, [task("a", "queued")])).toEqual([]);
    expect(settledTaskIds(prev, [task("b", "done")])).toEqual([]);
  });

  it("全部完成时返回全部", () => {
    const prev = new Set(["a", "b"]);
    expect(settledTaskIds(prev, [task("a", "done"), task("b", "error")]).sort()).toEqual(["a", "b"]);
  });
});
