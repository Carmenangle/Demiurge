import { describe, it, expect } from "vitest";
import { DeletedMessageTombstones } from "./deletedMessageTombstones";
import { upsertMessages } from "./chatSessionEvents";
import type { ChatMessage } from "../types/chat";

const msg = (id: string, text: string, role: ChatMessage["role"] = "assistant"): ChatMessage => ({
  id, role, text,
});

describe("DeletedMessageTombstones", () => {
  it("filterDeleted 过滤已删 id，保留其余（含无 id 项）", () => {
    const tomb = new DeletedMessageTombstones();
    tomb.record("repo-1", "floor-1");
    const items = [msg("floor-1", "已删楼层"), msg("u1", "用户消息"), { text: "无id项" } as ChatMessage];
    const kept = tomb.filterDeleted("repo-1", items);
    expect(kept.map((m) => m.id)).toEqual(["u1", undefined]);
  });

  it("未知 thread 或空墓碑原样放行（浅拷贝）", () => {
    const tomb = new DeletedMessageTombstones();
    const items = [msg("a", "x")];
    expect(tomb.filterDeleted("repo-x", items)).toEqual(items);
    expect(tomb.filterDeleted("repo-x", items)).not.toBe(items);
  });

  it("clear 后同 id 可回灌（导入=显式以后端为真源）", () => {
    const tomb = new DeletedMessageTombstones();
    tomb.record("repo-1", "floor-1");
    expect(tomb.filterDeleted("repo-1", [msg("floor-1", "x")])).toEqual([]);
    tomb.clear("repo-1");
    expect(tomb.filterDeleted("repo-1", [msg("floor-1", "x")])).toHaveLength(1);
  });

  it("墓碑按 thread 隔离", () => {
    const tomb = new DeletedMessageTombstones();
    tomb.record("repo-1", "floor-1");
    expect(tomb.filterDeleted("repo-2", [msg("floor-1", "x")])).toHaveLength(1);
  });
});

describe("画布删楼层竞态回归：删除后旧快照回灌不得复活已删消息", () => {
  it("recoverAgentRun 式全量 upsert（旧快照晚到）不再把刚删的楼层合并回状态", () => {
    const tomb = new DeletedMessageTombstones();
    tomb.record("repo-1", "floor-1");
    // 前端状态：楼层已删，仅剩用户消息
    const current: ChatMessage[] = [msg("u1", "输入", "user")];
    // 恢复轮询拉到的旧快照（删除落库前读取，仍含楼层）
    const staleSnapshot: ChatMessage[] = [msg("u1", "输入", "user"), msg("floor-1", "楼层正文")];
    const next = upsertMessages(current, tomb.filterDeleted("repo-1", staleSnapshot));
    expect(next.some((m) => m.id === "floor-1")).toBe(false);
    expect(next).toHaveLength(1);
  });

  it("未过滤的旧行为会复活楼层（钉死缺陷形态，防回归语义漂移）", () => {
    const current: ChatMessage[] = [msg("u1", "输入", "user")];
    const staleSnapshot: ChatMessage[] = [msg("u1", "输入", "user"), msg("floor-1", "楼层正文")];
    const next = upsertMessages(current, staleSnapshot);
    expect(next.some((m) => m.id === "floor-1")).toBe(true); // 旧 upsert 语义：只增不删
  });
});
