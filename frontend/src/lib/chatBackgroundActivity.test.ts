import { describe, expect, it } from "vitest";
import { finishedBackgroundThreads, mergeChatActivities } from "./chatBackgroundActivity";
import type { ChatQueueTask } from "../api/ai";

const task = (over: Partial<ChatQueueTask>): ChatQueueTask => ({
  id: "t", thread_id: "repo-1", need: "画只猫", status: "queued",
  created_at: 1, updated_at: 1, ...over,
});
const label = (id: string) => (id === "repo-1" ? "小仓库A" : id);

describe("mergeChatActivities", () => {
  it("lists running threads and queued tasks with repo labels", () => {
    const items = mergeChatActivities(
      ["repo-1"],
      [task({ id: "q1", thread_id: "repo-1", need: "第二条" })],
      label,
    );
    expect(items).toEqual([
      { kind: "running", threadId: "repo-1", label: "小仓库A", need: "" },
      { kind: "queued", threadId: "repo-1", label: "小仓库A", need: "第二条", taskId: "q1" },
    ]);
  });

  it("只给刚结束且当前没在看的线程补未读", () => {
    expect(finishedBackgroundThreads(
      ["repo-1__chat__甲", "repo-1__chat__乙"],
      ["repo-1__chat__乙"],
      "repo-1__chat__乙",
    )).toEqual(["repo-1__chat__甲"]);
    // 当前正在看的线程跑完 → 不补红点（用户就在里面）
    expect(finishedBackgroundThreads(["a"], [], "a")).toEqual([]);
    // 仍在跑 / home 草稿 → 不补
    expect(finishedBackgroundThreads(["a", "home"], ["a"], "")).toEqual([]);
  });

  it("excludes home draft thread from both running and queued", () => {
    const items = mergeChatActivities(
      ["home"],
      [task({ thread_id: "home" })],
      label,
    );
    expect(items).toEqual([]);
  });

  it("does not double-list a running queue task (running-threads already covers it)", () => {
    const items = mergeChatActivities(
      ["repo-1"],
      [task({ id: "r1", thread_id: "repo-1", status: "running" })],
      label,
    );
    expect(items).toEqual([
      { kind: "running", threadId: "repo-1", label: "小仓库A", need: "" },
    ]);
  });

  it("ignores terminal queue tasks", () => {
    const items = mergeChatActivities(
      [],
      [
        task({ id: "d", status: "done" }),
        task({ id: "e", status: "error" }),
        task({ id: "c", status: "cancelled" }),
      ],
      label,
    );
    expect(items).toEqual([]);
  });
});
