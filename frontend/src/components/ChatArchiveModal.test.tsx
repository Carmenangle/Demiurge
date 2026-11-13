// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";

import { ChatArchiveModal } from "./ChatArchiveModal";
import { fetchChatArchives, restoreChatArchive } from "../api/ai";

// 「恢复对话」弹窗的回归网（2026-11-05 防误删 ①）。钉四件事：
// ① 列表按后端给的 时间/原因/条数 渲染，**没点确认之前一个恢复请求都不发**——界面上这一刀
//    必须和后端那道 fail-closed 归档闸同方向，否则软删层等于摆设；
// ② 确认后才覆盖，且覆盖完必须走「以后端为真源」的重读（onRestored＝清墓碑再回读）再关窗：
//    顺序反了或漏了，界面会停在旧内容上，用户以为恢复没生效；
// ③ 后端报忙（409）时文案可见、窗口不关、不重读（别把会话刷成半恢复状态）；
// ④ 原因码没登记时原样显示，不替用户猜成别的动作。

vi.mock("../api/ai", async () => {
  const actual = await vi.importActual<Record<string, unknown>>("../api/ai");
  return { ...actual, fetchChatArchives: vi.fn(), restoreChatArchive: vi.fn() };
});

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ARCHIVES = [
  { name: "20261105-101500-123-compact-9", reason: "compact", message_count: 9, created_at: "2026-11-05 10:15:00", bytes: 2097152 },
  { name: "20261105-090000-001-clear-cache-40", reason: "clear-cache", message_count: 40, created_at: "2026-11-05 09:00:00", bytes: 512 },
];

let container: HTMLDivElement;
let root: Root;
let order: string[];

async function mount(el: ReactElement) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(el); });
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.clearAllMocks();
});

function text(): string {
  return container.textContent || "";
}

function button(label: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button"))
    .find((b) => (b.textContent || "").includes(label));
  if (!found) throw new Error(`button not found: ${label}`);
  return found as HTMLButtonElement;
}

/** 挂载弹窗；props 可按用例覆写（onRestored/onClose 都记进 order 用于断言时序）。 */
async function open(
  items = ARCHIVES,
  over: {
    restore?: () => Promise<{ ok: boolean; messages: number; history: number }>;
    onRestored?: () => Promise<void>;
  } = {},
) {
  order = [];
  const onRestored = over.onRestored ?? (async () => { order.push("reload"); });
  const onClose = vi.fn(async () => { order.push("close"); });
  vi.mocked(fetchChatArchives).mockResolvedValue({ items });
  vi.mocked(restoreChatArchive).mockImplementation(
    over.restore ?? (async () => { order.push("restore"); return { ok: true, messages: 9, history: 2 }; }),
  );
  const props = { threadId: "会话甲", onRestored, onNotify: vi.fn(), onClose };
  await mount(<ChatArchiveModal {...props} />);
  return props;
}

describe("ChatArchiveModal", () => {
  it("列出归档的时间/原因/条数，没点确认前一个恢复请求都不发", async () => {
    await open();
    expect(fetchChatArchives).toHaveBeenCalledWith("会话甲");
    expect(text()).toContain("2026-11-05 10:15:00");
    expect(text()).toContain("压缩会话");
    expect(text()).toContain("清除缓存");
    expect(text()).toContain("40 条");
    expect(text()).toContain("2.0 MB");
    expect(restoreChatArchive).not.toHaveBeenCalled();
  });

  it("未知原因码原样显示（不替用户猜成别的动作）", async () => {
    await open([{ ...ARCHIVES[0], reason: "mystery", created_at: "2026-11-05 11:00:00" }]);
    expect(text()).toContain("mystery");
  });

  it("点「恢复」只弹二次确认；确认后才覆盖，且先重读会话再关窗", async () => {
    await open();
    await act(async () => { button("恢复").click(); });
    expect(restoreChatArchive).not.toHaveBeenCalled();
    expect(text()).toContain("用这份归档覆盖当前会话？");
    expect(text()).toContain("资产库与知识库不受影响");

    await act(async () => { button("恢复这份").click(); });
    expect(order).toEqual(["restore", "reload", "close"]);   // 覆盖 → 重读 → 关窗，顺序不能反
    expect(restoreChatArchive).toHaveBeenCalledWith("会话甲", "20261105-101500-123-compact-9");
  });

  it("确认框取消＝回到列表且什么都没发生", async () => {
    await open();
    await act(async () => { button("恢复").click(); });
    await act(async () => { button("取消").click(); });
    expect(restoreChatArchive).not.toHaveBeenCalled();
    expect(order).toEqual([]);
    expect(text()).toContain("恢复对话");
  });

  it("后端报忙（409）时文案可见、不关窗、不重读会话", async () => {
    await open(ARCHIVES, {
      restore: async () => {
        order.push("restore");
        throw new Error("该对话仍有后台生成任务，请等待完成或先停止");
      },
    });
    await act(async () => { button("恢复").click(); });
    await act(async () => { button("恢复这份").click(); });
    expect(text()).toContain("恢复失败：该对话仍有后台生成任务");
    expect(text()).toContain("2026-11-05 09:00:00");        // 列表还在，可换一份再试
    expect(order).toEqual(["restore"]);                      // 无 reload、无 close
  });

  it("还没有归档时说清什么时候才会有", async () => {
    await open([]);
    expect(text()).toContain("这个会话还没有归档");
  });
});
