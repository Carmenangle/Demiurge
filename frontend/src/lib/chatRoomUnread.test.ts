import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "laf_chatroom_unread";

/** Node 25 自带实验性 localStorage（无 clear），与浏览器 Storage 行为不一致；
 *  用内存替身统一两种运行时的语义，测试只依赖 getItem/setItem/removeItem/clear。 */
class MemoryStorage {
  private data = new Map<string, string>();
  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.data.set(key, String(value));
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
  clear(): void {
    this.data.clear();
  }
}

// 模块内 state 在 import 时从 localStorage 载入 ⇒ 每个用例重新导入，互不串状态。
async function loadModule() {
  vi.resetModules();
  return import("./chatRoomUnread");
}

describe("聊天室未读（模式条与头像红点真源）", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", new MemoryStorage());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("按线程 + 发言人累加，私聊与群聊互不串线", async () => {
    const unread = await loadModule();
    unread.addUnread("r1__chat", "甲");
    unread.addUnread("r1__chat", "甲");
    unread.addUnread("r1__group", "乙");

    expect(unread.getUnread("r1__chat")).toEqual({ total: 2, speakers: { 甲: 2 } });
    expect(unread.getUnread("r1__group")).toEqual({ total: 1, speakers: { 乙: 1 } });
    expect(unread.getUnread("r2__chat")).toEqual({ total: 0, speakers: {} });
  });

  it("清除单个发言人只减该角色，其他角色红点保留", async () => {
    const unread = await loadModule();
    unread.addUnread("r1__group", "甲", 2);
    unread.addUnread("r1__group", "乙");

    unread.clearSpeakerUnread("r1__group", "甲");

    expect(unread.getUnread("r1__group")).toEqual({ total: 1, speakers: { 乙: 1 } });
  });

  it("最后一位发言人也清掉后整条线程归零（模式条红点消失）", async () => {
    const unread = await loadModule();
    unread.addUnread("r1__chat", "甲");
    unread.clearSpeakerUnread("r1__chat", "甲");

    expect(unread.getUnread("r1__chat").total).toBe(0);
    expect(localStorage.getItem(KEY)).toBe("{}");
  });

  it("clearUnread 整线程清空，订阅者收到通知", async () => {
    const unread = await loadModule();
    const listener = vi.fn();
    const off = unread.subscribeUnread(listener);
    unread.addUnread("r1__chat", "甲");
    unread.clearUnread("r1__chat");

    expect(listener).toHaveBeenCalledTimes(2);
    expect(unread.getUnread("r1__chat").total).toBe(0);
    off();
  });

  it("空线程 id / 非正数计数被忽略；空发言人只记总数不落发言人红点", async () => {
    const unread = await loadModule();
    unread.addUnread("", "甲");
    unread.addUnread("r1__chat", "甲", 0);
    unread.addUnread("r1__chat", "");

    expect(unread.getUnread("")).toEqual({ total: 0, speakers: {} });
    expect(unread.getUnread("r1__chat")).toEqual({ total: 1, speakers: {} });
  });

  it("坏 JSON 不炸模块，读回空状态（本机提示写坏不阻断聊天）", async () => {
    localStorage.setItem(KEY, "{not-json");
    const unread = await loadModule();
    expect(unread.getUnread("r1__chat")).toEqual({ total: 0, speakers: {} });
  });
});
