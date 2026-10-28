// @vitest-environment jsdom
import { StrictMode, act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { overlayIdsOf, stripOverlaySegments, useOverlayRoute, withOverlaySegments } from "./overlayRoute";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function Probe({ id, onClose }: { id: string | null; onClose: () => void }) {
  useOverlayRoute(id, onClose);
  return null;
}

let container: HTMLDivElement | null = null;
let root: Root | null = null;
const BASE = "#/assets/works";

function mount(node: ReactNode) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root!.render(node));
}

function rerender(node: ReactNode) {
  act(() => root!.render(node));
}

async function settle(ms = 40) {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, ms)); });
}

beforeEach(() => {
  // pushState 会截断「前进」历史；每个用例都从一条干净的基线开始。
  window.history.pushState(null, "", BASE);
});

afterEach(async () => {
  if (root) act(() => root!.unmount());
  await settle();
  if (container) container.remove();
  root = null;
  container = null;
});

describe("弹窗段解析", () => {
  it("视图 hash 没有弹窗段；链式弹窗按开着的顺序返回", () => {
    expect(overlayIdsOf("")).toEqual([]);
    expect(overlayIdsOf("#/assets/works")).toEqual([]);
    expect(overlayIdsOf("#/assets/works/@asset-detail/@lightbox")).toEqual(["asset-detail", "lightbox"]);
  });

  it("去掉弹窗段只剩视图 hash；纯弹窗 hash 退回 #", () => {
    expect(stripOverlaySegments("#/settings/paths/@regex")).toBe("#/settings/paths");
    expect(stripOverlaySegments("#/@lightbox")).toBe("#");
    expect(stripOverlaySegments("#")).toBe("#");
  });

  it("按弹窗链重建 hash（空链即视图 hash；id 带百分号转义）", () => {
    expect(withOverlaySegments("#/settings/paths", [])).toBe("#/settings/paths");
    expect(withOverlaySegments("#/settings/paths", ["a", "b"])).toBe("#/settings/paths/@a/@b");
    expect(withOverlaySegments("#/assets/works", ["发送 至"])).toBe("#/assets/works/@%E5%8F%91%E9%80%81%20%E8%87%B3");
    expect(overlayIdsOf(withOverlaySegments("#/assets/works", ["发送 至"]))).toEqual(["发送 至"]);
  });

  it("畸形百分号不抛", () => {
    expect(() => overlayIdsOf("#/x/@%zz")).not.toThrow();
    expect(overlayIdsOf("#/x/@%zz")).toEqual(["%zz"]);
  });
});

describe("useOverlayRoute 接返回链", () => {
  it("开弹窗压一段历史，视图 hash 保持原样", () => {
    const onClose = vi.fn();
    mount(<Probe id="lightbox" onClose={onClose} />);

    expect(window.location.hash).toBe(`${BASE}/@lightbox`);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("返回键（历史后退）关弹窗，而不是退掉底下的页面", async () => {
    const onClose = vi.fn();
    mount(<Probe id="regex" onClose={onClose} />);

    window.history.back();
    await settle();

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(window.location.hash).toBe(BASE);
  });

  it("程序化关闭时把自己压的那条历史退掉，不留空按一格", async () => {
    const onClose = vi.fn();
    mount(<Probe id="preset" onClose={onClose} />);
    rerender(<Probe id={null} onClose={onClose} />);
    await settle();

    expect(window.location.hash).toBe(BASE);
    expect(overlayIdsOf(window.location.hash)).toEqual([]);
  });

  it("StrictMode 重挂只压一条历史，也不会误关", async () => {
    const onClose = vi.fn();
    mount(<StrictMode><Probe id="send-to-chat" onClose={onClose} /></StrictMode>);
    await settle();

    expect(overlayIdsOf(window.location.hash)).toEqual(["send-to-chat"]);
    expect(onClose).not.toHaveBeenCalled();
  });

  it("叠开的弹窗从顶往下逐个关", async () => {
    const closeA = vi.fn();
    const closeB = vi.fn();
    mount(<><Probe id="asset-detail" onClose={closeA} /><Probe id="workflow-tool" onClose={closeB} /></>);
    expect(overlayIdsOf(window.location.hash)).toEqual(["asset-detail", "workflow-tool"]);

    window.history.back();
    await settle();
    expect(closeB).toHaveBeenCalledTimes(1);
    expect(closeA).not.toHaveBeenCalled();
    expect(overlayIdsOf(window.location.hash)).toEqual(["asset-detail"]);

    window.history.back();
    await settle();
    expect(closeA).toHaveBeenCalledTimes(1);
    expect(overlayIdsOf(window.location.hash)).toEqual([]);
  });
});
