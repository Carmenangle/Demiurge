import { afterEach, describe, expect, it, vi } from "vitest";

import {
  CHAT_MEDIA_TASK_STALE_MS,
  cancelChatMediaTask,
  chatMediaTasksForThread,
  clearChatMediaResume,
  finishChatMediaTask,
  loadChatMediaResume,
  saveChatMediaResume,
  setChatMediaTaskPhase,
  startChatMediaTask,
  subscribeChatMediaTasks,
} from "./chatMediaActivity";

describe("chatMediaActivity", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("drops a task that has been running longer than the stale window", () => {
    // 最坏路径＝等 GPU 空闲 10 分钟 + 图片轮询 20 分钟，阈值必须覆盖完整重试。
    expect(CHAT_MEDIA_TASK_STALE_MS).toBeGreaterThan(30 * 60 * 1000);
    vi.useFakeTimers();
    vi.setSystemTime(0);
    startChatMediaTask({ threadId: "stale-thread", speaker: "甲", kind: "image" });
    expect(chatMediaTasksForThread("stale-thread")).toHaveLength(1);

    vi.setSystemTime(CHAT_MEDIA_TASK_STALE_MS + 1);
    expect(chatMediaTasksForThread("stale-thread")).toHaveLength(0);
  });

  it("finish removes the task immediately", () => {
    startChatMediaTask({ threadId: "finish-thread", speaker: "乙", kind: "video" });
    finishChatMediaTask("finish-thread", "乙");
    expect(chatMediaTasksForThread("finish-thread")).toHaveLength(0);
  });

  it("第一次失败后切到 waiting，重拍开始再切回 shooting", () => {
    let notified = 0;
    const off = subscribeChatMediaTasks(() => { notified += 1; });
    startChatMediaTask({ threadId: "wait-thread", speaker: "丙", kind: "image" });

    setChatMediaTaskPhase("wait-thread", "丙", "waiting");
    expect(chatMediaTasksForThread("wait-thread")[0].phase).toBe("waiting");
    expect(notified).toBeGreaterThanOrEqual(2);

    setChatMediaTaskPhase("wait-thread", "丙", "shooting");
    expect(chatMediaTasksForThread("wait-thread")[0].phase).toBe("shooting");
    off();
    finishChatMediaTask("wait-thread", "丙");
  });

  it("旧任务的收尾不会删掉同角色后来新起的拍摄", () => {
    const first = startChatMediaTask({
      threadId: "task-id-thread", speaker: "甲", kind: "image",
    });
    finishChatMediaTask("task-id-thread", "甲", first);
    const second = startChatMediaTask({
      threadId: "task-id-thread", speaker: "甲", kind: "image",
    });

    expect(finishChatMediaTask("task-id-thread", "甲", first)).toBe(false);
    expect(chatMediaTasksForThread("task-id-thread")).toHaveLength(1);

    finishChatMediaTask("task-id-thread", "甲", second);
  });

  it("取消拍摄会移除任务并通知取消回调", () => {
    const cancel = vi.fn();
    const id = startChatMediaTask({
      threadId: "cancel-thread", speaker: "丙", kind: "image", cancel,
    });

    expect(cancelChatMediaTask("cancel-thread", "丙")).toBe(true);
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(chatMediaTasksForThread("cancel-thread")).toHaveLength(0);
    expect(finishChatMediaTask("cancel-thread", "丙", id)).toBe(false);
  });

  it("持久化续接记录，并按僵尸窗口过期", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => { store.set(key, value); },
      removeItem: (key: string) => { store.delete(key); },
      clear: () => { store.clear(); },
      key: () => null,
      length: 0,
    });
    vi.useFakeTimers();
    vi.setSystemTime(0);
    saveChatMediaResume({
      threadId: "resume-thread", speaker: "甲", promptId: "p1",
      prompt: "海边泳装", createdAt: 0,
    });
    expect(loadChatMediaResume("resume-thread", "甲")?.promptId).toBe("p1");

    vi.setSystemTime(CHAT_MEDIA_TASK_STALE_MS + 1);
    expect(loadChatMediaResume("resume-thread", "甲")).toBeNull();
    clearChatMediaResume("resume-thread", "甲");
  });
});
