// @vitest-environment jsdom
// 节点卡内容守卫回归：帧内画的不是本节点时必须重新下发 load 自愈。
// 旧逻辑只数「画布里是否恰好 1 个节点」，被兄弟 iframe 污染的画布同样是 1 个节点
// → 误判成功、永远停在「节点 3 的框里是节点 2」。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("../api/comfyui", () => ({
  comfyStatus: vi.fn(async () => ({ running: true })),
  uploadImage: vi.fn(async () => ({ name: "x.png" })),
}));

import { NodeCard } from "./WorkflowCard";

const COMFY = "http://127.0.0.1:8188";
const WF = { nodes: [{ id: 18, type: "KSampler" }, { id: 20, type: "EmptyLatentImage" }] };

let container: HTMLDivElement;
let root: Root;

function frame(): HTMLIFrameElement {
  const el = container.querySelector("iframe");
  if (!el) throw new Error("iframe 未渲染");
  return el as HTMLIFrameElement;
}

// 以 iframe 身份向父页面回一条 laf_lock 消息（走真实来源校验，不作弊）
function reply(type: string, payload?: unknown) {
  const win = frame().contentWindow;
  if (!win) throw new Error("iframe contentWindow 为空");
  act(() => {
    window.dispatchEvent(
      new MessageEvent("message", {
        source: win as unknown as MessageEventSource,
        origin: COMFY,
        data: { source: "laf_lock", type, payload },
      }),
    );
  });
}

function postedTypes(): string[] {
  const pm = (frame().contentWindow as unknown as { postMessage: { mock: { calls: any[][] } } })
    .postMessage;
  return pm.mock.calls.map((c) => (c[0] as { type: string }).type);
}

async function mountCard(nodeId: string) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<NodeCard cardId="card-1" nodeId={nodeId} index={2} workflow={WF} comfyUrl={COMFY} />);
  });
  // 等 comfyStatus 轮询（async）把 comfyReady 置真、iframe 渲染出来
  for (let i = 0; i < 5 && !container.querySelector("iframe"); i++) {
    await act(async () => { await Promise.resolve(); });
  }
  vi.spyOn(frame().contentWindow as Window, "postMessage");
  reply("ready");    // 触发首次 load
  reply("loaded");   // 触发首检排程
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("NodeCard 内容守卫", () => {
  it("帧内是本节点 → 校验通过，不再重发 load", async () => {
    await mountCard("20");
    expect(postedTypes()).toEqual(["load"]);
    await act(async () => { vi.advanceTimersByTime(2300); });   // 首检（loaded 后 2.2s）
    expect(postedTypes()).toEqual(["load", "request_graph"]);
    reply("graph", { workflow: { nodes: [{ id: 20, type: "EmptyLatentImage" }] } });
    expect(postedTypes()).toEqual(["load", "request_graph"]);   // 通过 → 不重发
  });

  it("帧内是别的节点（节点3显示了节点2）→ 重发 load 自愈", async () => {
    await mountCard("20");
    await act(async () => { vi.advanceTimersByTime(2300); });
    reply("graph", { workflow: { nodes: [{ id: 18, type: "KSampler" }] } });
    // 旧行为：count===1 判成功 → 只会停在 request_graph；修复后必须补一条 load
    expect(postedTypes()).toEqual(["load", "request_graph", "load"]);
  });

  it("尺寸回传不是本节点的 → 丢弃，不改本卡比例", async () => {
    await mountCard("20");
    reply("node_size", { id: 18, w: 400, h: 400 });
    expect((container.querySelector(".lock-canvas") as HTMLElement).style.aspectRatio).toBe("");
    reply("node_size", { id: 20, w: 400, h: 200 });
    // jsdom 会把 "2" 规范化成 "2 / 1"
    expect((container.querySelector(".lock-canvas") as HTMLElement).style.aspectRatio).toBe("2 / 1");
  });
});
